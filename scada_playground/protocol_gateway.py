"""Expose the live synthetic plant snapshot through Modbus TCP, OPC UA, MQTT."""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import struct
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from amqtt.broker import Broker
from asyncua import Server, ua
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pymodbus.datastore import ModbusDeviceContext, ModbusServerContext, ModbusSequentialDataBlock
from pymodbus.server import ModbusTcpServer
import uvicorn

from protocol_faults import ProtocolFaultInjector


ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / ".runtime" / "factory_snapshot.json"
REGISTER_MAP = ROOT / ".runtime" / "modbus_register_map.csv"
NAMESPACE = "https://scada-learning-lab.local/factory"
AREA_ROOT = "scada/factory/areas"
QUALITY_CODE = {"GOOD": 0, "STALE": 1, "BAD": 2}
live_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

MAP_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{height:100%;margin:0;font:14px system-ui,sans-serif} #map{background:#e7ecef}
.status{position:absolute;z-index:1000;left:12px;top:12px;padding:8px 12px;border-radius:8px;background:#fff;box-shadow:0 2px 10px #0002;color:#25313a}
.vehicle{font-size:19px;line-height:25px;text-align:center;text-shadow:0 1px 4px white,1px 0 4px white,-1px 0 4px white}
.legend{position:absolute;z-index:1000;right:12px;bottom:22px;background:#fff;padding:8px 11px;border-radius:7px;box-shadow:0 2px 10px #0002;line-height:1.7}
.swatch{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
</style></head><body><div id="map"></div><div class="status" id="status">Connecting to live fleet…</div>
<div class="legend"><span class="swatch" style="background:#e76f51"></span>Haul truck<br><span class="swatch" style="background:#577590"></span>Service truck<br><span class="swatch" style="background:#43aa8b"></span>Site bus</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const center=[-24.277,-69.067], colors={"Haul truck":"#e76f51","Service truck":"#577590","Site bus":"#43aa8b"};
const map=L.map('map',{zoomControl:true,preferCanvas:true}).setView(center,12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
const routeLayer=L.layerGroup().addTo(map), siteLayer=L.layerGroup().addTo(map), markers=new Map();
const sites=[['Open pit',-24.2910,-69.0810],['Crusher',-24.2780,-69.0670],['Workshop',-24.2730,-69.0700],['Substation',-24.2735,-69.0590],['Water pumps',-24.2660,-69.0620],['Operations',-24.2740,-69.0690]];
for(const [name,lat,lon] of sites)L.circleMarker([lat,lon],{radius:6,color:'#22223b',fillColor:'#fff',fillOpacity:1,weight:3}).bindTooltip(name,{permanent:true,direction:'top'}).addTo(siteLayer);
let routesReady=false;
function animate(marker, from, to, duration=850){const start=performance.now();function frame(now){const t=Math.min(1,(now-start)/duration);marker.setLatLng([from[0]+(to[0]-from[0])*t,from[1]+(to[1]-from[1])*t]);if(t<1)requestAnimationFrame(frame)}requestAnimationFrame(frame)}
let lastFrameKey=null;
function connect(){const ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/fleet');
 ws.onmessage=event=>{const data=JSON.parse(event.data);const frameKey=`${data.sim_seconds}:${data.running}`;if(frameKey===lastFrameKey)return;lastFrameKey=frameKey;
  if(!routesReady){for(const route of data.routes||[]){const latlngs=route.points.map(p=>[p.latitude,p.longitude]);latlngs.push(latlngs[0]);L.polyline(latlngs,{color:colors[route.vehicle_type]||'#666',weight:4,opacity:.76}).addTo(routeLayer)}routesReady=true}
  for(const v of data.fleet||[]){const color=colors[v.vehicle_type]||'#555', point=[v.latitude,v.longitude];let marker=markers.get(v.unit);
   const icon=L.divIcon({className:'',html:`<div class="vehicle" style="color:${color};transform:rotate(${v.heading}deg)">▲</div>`,iconSize:[22,24],iconAnchor:[11,12]});
   if(!marker){marker=L.marker(point,{icon}).addTo(map);markers.set(v.unit,marker)}else{const from=marker.getLatLng();if(Math.abs(from.lat-point[0])+Math.abs(from.lng-point[1])>0.000001){marker.setIcon(icon);animate(marker,[from.lat,from.lng],point)}}
   marker.bindPopup(`<b>${v.unit}</b><br>${v.state}<br>Speed: ${Number(v.speed_kmh).toFixed(1)} km/h<br>Payload: ${Number(v.payload_t).toFixed(0)} t`);
  }
  const t=Number(data.sim_seconds||0),status=`${data.running?'● SIM RUNNING':'○ SIM STOPPED'} · ${data.fleet.length} vehicles · ${String(Math.floor(t/60)).padStart(2,'0')}:${String(t%60).padStart(2,'0')} sim time`;
  if(document.getElementById('status').textContent!==status)document.getElementById('status').textContent=status;
 };
 ws.onclose=()=>{document.getElementById('status').textContent='Live stream disconnected · reconnecting…';setTimeout(connect,1500)};
 ws.onerror=()=>ws.close();
}connect();
</script></body></html>"""


@live_app.get("/map", response_class=HTMLResponse)
async def live_map_page() -> str:
    return MAP_PAGE


@live_app.websocket("/ws/fleet")
async def fleet_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            snapshot = load_snapshot()
            await websocket.send_json({"timestamp": snapshot["timestamp"], "sim_seconds": snapshot["sim_seconds"],
                                      "running": snapshot["running"], "fleet": snapshot["fleet"],
                                      "routes": snapshot["routes"]})
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def float_registers(value: float) -> tuple[int, int]:
    high, low = struct.unpack(">HH", struct.pack(">f", float(value)))
    return high, low


def write_modbus(context: ModbusServerContext, tags: list[dict]) -> None:
    words: list[int] = []
    for tag in tags:
        words.extend(float_registers(tag["value"]))
        words.append(QUALITY_CODE.get(tag["quality"], 2))
    # ModbusDeviceContext shifts client addresses by one internally; the block
    # begins at 1 so client input-register address 0 maps to the first tag.
    context[1].setValues(4, 0, words)


def write_register_map(tags: list[dict]) -> None:
    REGISTER_MAP.parent.mkdir(parents=True, exist_ok=True)
    with REGISTER_MAP.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["tag", "value_float32_high_register", "value_float32_low_register", "quality_register", "units"])
        for index, tag in enumerate(tags):
            address = index * 3
            writer.writerow([tag["tag"], address, address + 1, address + 2, tag["units"]])


def slug(value: str) -> str:
    return "_".join(value.lower().replace("&", "and").replace("/", "_").split())


async def build_opcua() -> tuple[Server, dict[str, tuple]]:
    snapshot = load_snapshot()
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://127.0.0.1:4840/factory/server/")
    idx = await server.register_namespace(NAMESPACE)
    factory = await server.nodes.objects.add_object(
        ua.NodeId("Factory", idx), ua.QualifiedName("Factory", idx)
    )
    area_nodes: dict[str, object] = {}
    asset_nodes: dict[tuple[str, str], object] = {}
    references = {}
    for tag in snapshot["tags"]:
        area = tag["area"]
        asset = tag["asset"]
        if area not in area_nodes:
            area_nodes[area] = await factory.add_object(
                ua.NodeId(f"Area/{slug(area)}", idx), ua.QualifiedName(area, idx)
            )
        asset_key = (area, asset)
        if asset_key not in asset_nodes:
            asset_nodes[asset_key] = await area_nodes[area].add_object(
                ua.NodeId(f"Asset/{slug(area)}/{slug(asset)}", idx), ua.QualifiedName(asset, idx)
            )
        tag_object = await asset_nodes[asset_key].add_object(
            ua.NodeId(f"Tag/{tag['tag']}", idx), ua.QualifiedName(tag["sensor"], idx)
        )
        value_node = await tag_object.add_variable(
            ua.NodeId(f"Value/{tag['tag']}", idx), ua.QualifiedName("Value", idx), float(tag["value"])
        )
        quality_node = await tag_object.add_variable(
            ua.NodeId(f"Quality/{tag['tag']}", idx), ua.QualifiedName("Quality", idx), str(tag["quality"])
        )
        units_node = await tag_object.add_variable(
            ua.NodeId(f"Units/{tag['tag']}", idx), ua.QualifiedName("Units", idx), str(tag["units"])
        )
        references[tag["tag"]] = (value_node, quality_node, units_node)
    return server, references


def mqtt_publish(client: mqtt.Client, snapshot: dict) -> None:
    timestamp = snapshot["timestamp"]
    grouped: dict[str, list[dict]] = {}
    for tag in snapshot["tags"]:
        grouped.setdefault(tag["area"], []).append({
            "tag": tag["tag"], "asset": tag["asset"], "sensor": tag["sensor"],
            "value": tag["value"], "units": tag["units"], "quality": tag["quality"],
        })
    for area, tags in grouped.items():
        slug = area.lower().replace(" ", "_").replace("&", "and").replace("/", "_")
        client.publish(f"{AREA_ROOT}/{slug}/telemetry",
                       json.dumps({"timestamp": timestamp, "tags": tags}, separators=(",", ":")),
                       qos=0, retain=True)
    client.publish("scada/factory/status", json.dumps({
        "timestamp": timestamp, "sim_seconds": snapshot["sim_seconds"], "running": snapshot["running"],
        "tag_count": len(snapshot["tags"]),
    }, separators=(",", ":")), qos=1, retain=True)
    client.publish("scada/factory/alarms/state", json.dumps({
        "timestamp": timestamp, "alarms": snapshot["alarms"],
    }, separators=(",", ":")), qos=1, retain=True)
    client.publish("scada/factory/faults/protocol", json.dumps({
        "timestamp": timestamp, "faults": snapshot.get("protocol_faults", []),
    }, separators=(",", ":")), qos=1, retain=True)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    if not SNAPSHOT.exists():
        raise SystemExit("Start the Streamlit simulator first; its snapshot file is missing.")
    initial = load_snapshot()
    write_register_map(initial["tags"])

    # FC04 input registers make this Modbus endpoint read-only by function code.
    modbus_block = ModbusSequentialDataBlock(1, [0] * (3 * len(initial["tags"])))
    device = ModbusDeviceContext(ir=modbus_block)
    context = ModbusServerContext(devices={1: device}, single=False)
    write_modbus(context, initial["tags"])
    modbus = ModbusTcpServer(context, address=("127.0.0.1", 1502))
    await modbus.serve_forever(background=True)

    broker = Broker(config={
        "listeners": {"default": {"type": "tcp", "bind": "127.0.0.1:1883"}},
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    })
    await broker.start()
    live_server = uvicorn.Server(uvicorn.Config(live_app, host="127.0.0.1", port=8502,
                                                log_level="warning", access_log=False))
    live_task = asyncio.create_task(live_server.serve())
    while not live_server.started:
        if live_task.done():
            raise RuntimeError("Live map WebSocket server failed to start on 127.0.0.1:8502")
        await asyncio.sleep(0.05)
    publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="scada-playground-publisher")
    publisher.connect("127.0.0.1", 1883, keepalive=30)
    publisher.loop_start()

    server, opc_nodes = await build_opcua()
    fault_injector = ProtocolFaultInjector()
    logging.info("Modbus TCP listening on 127.0.0.1:1502, unit ID 1 (FC04 input registers)")
    logging.info("OPC UA listening on opc.tcp://127.0.0.1:4840/factory/server/")
    logging.info("MQTT broker listening on 127.0.0.1:1883; publishing scada/factory/#")
    logging.info("Live fleet map stream listening on http://127.0.0.1:8502/map (WebSocket: /ws/fleet)")
    logging.info("Register map: %s", REGISTER_MAP)

    try:
        async with server:
            while True:
                snapshot = load_snapshot()
                fault_injector.sync(snapshot)
                modbus_view = fault_injector.apply(snapshot, "Modbus TCP")
                opcua_view = fault_injector.apply(snapshot, "OPC UA")
                mqtt_view = fault_injector.apply(snapshot, "MQTT")
                write_modbus(context, modbus_view["tags"])
                for tag in opcua_view["tags"]:
                    value_node, quality_node, _units_node = opc_nodes[tag["tag"]]
                    await value_node.write_value(float(tag["value"]))
                    await quality_node.write_value(str(tag["quality"]))
                mqtt_publish(publisher, mqtt_view)
                await asyncio.sleep(1.0)
    finally:
        publisher.loop_stop()
        publisher.disconnect()
        await broker.shutdown()
        await modbus.shutdown()
        live_server.should_exit = True
        await live_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Protocol gateway stopped.")
