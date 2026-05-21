# ESPHome Direct Gate Notes

These notes capture the proof of concept for controlling the ESPHome gate opener directly, without Home Assistant running.

## Confirmed Working

- Direct ESPHome native API access works while Home Assistant is completely shut down.
- Tested device IP: `10.0.107.22`
- Native API port: `6053`
- The ESPHome device requires API encryption.
- The encryption key should be entered at runtime only. Do not commit it to the repo.
- Leave the legacy API password blank unless the ESPHome YAML explicitly configures `api.password`.

The POC lives at:

```text
pocs/esphome-gate/
```

Run it with:

```sh
cd pocs/esphome-gate
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 127.0.0.1 --port 8099
```

Open:

```text
http://127.0.0.1:8099
```

## Important Implementation Detail

For this device, `aioesphomeapi` must finish the encrypted native API handshake without sending a legacy login request.

Working pattern:

```python
client = aioesphomeapi.APIClient(
    address=host,
    port=6053,
    password=None,
    noise_psk=encryption_key,
)

await client.start_resolve_host()
await client.start_connection()
await client.finish_connection(login=False)
entities, services = await client.list_entities_services()
```

Do not pass `password=""` and do not call `finish_connection(login=True)` unless a real legacy `api.password` exists. Doing so caused:

```text
Timeout waiting for HelloResponse, ConnectResponse after 30.0s
```

## Discovered Cover

The device currently exposes one cover:

```text
Name: Garage Door
Object ID: garage_door
Device class: garage
```

The cover state subscription reports live state correctly, including `closed`.

Opening is done with:

```python
client.cover_command(key=cover_key, position=1.0)
```

The POC requires browser confirmation before sending this command.

## Future IACS Integration Shape

If this becomes production code, keep it behind the existing gate abstraction instead of wiring ESPHome directly into access-event logic.

Recommended shape:

- Add an `esphome` implementation of `GateController`.
- Register it in the module registry beside `home_assistant`.
- Keep `GateCommandCoordinator` as the only path for physical gate opens.
- Store ESPHome host, port, encryption key, and optional cover selector in dynamic settings.
- Treat the encryption key as a secret setting.
- Add an ESPHome state listener or polling service that writes equivalent `GateStateObservation` rows so reconciliation and malfunction detection keep working without Home Assistant.

## Things Not To Do

- Do not store the ESPHome encryption key in markdown, logs, frontend code, or committed config.
- Do not use a Home Assistant token for ESPHome native API.
- Do not send a legacy login request with an empty password.
- Do not bypass `GateCommandCoordinator` for production gate commands.
