# Migrating from OpenJigWare (C#) to the Python SDK

Three routes, from least to most change:

1. **Run the scanner**: `genibo migrate Form1.cs -o migrated.py` lists every
   `CGenibo_Bt` / `CGenibo` / `COjwMotor.Mpsu_*` call with its line, the
   modern equivalent and caveats. It also writes a skeleton host script.
2. **Line-by-line port with the shim**: `genibo.legacy.CGeniboBt` keeps the
   legacy method names and transmits the same bytes (golden-tested, quirks
   included). See `examples/ex20_genibo/legacy_port.py`.
3. **Modern API**: `genibo.Genibo` for live control, `@program` for choreography.
   See `examples/ex20_genibo/src/main.py`.

## Name inversions to watch

| Legacy | Direction | SDK |
|---|---|---|
| `request_file_download` | PC → robot | `upload_file` |
| `request_file_upload` | robot → PC | `download_file` (returns bytes; does not write `motion\master\…`) |
| `nMaster_or_User` 0/1 | – | `Storage.MASTER` / `Storage.USER` |
| `connect(5, 115200)` | – | `Genibo.serial("COM5")`; on macOS `/dev/tty.<name>`, on Linux `/dev/rfcomm0` |

## API mapping (Bluetooth, `Ojw.CGenibo_Bt`)

| Legacy | Modern |
|---|---|
| `connect(port, baud)` / `disconnect()` | `Genibo.serial(port, baud).open()` / `.close()` (or `with`) |
| `drbluetooth_set_id(id)` | `Genibo(..., robot_id=id)` |
| `Cmd_Bluetooth_Play(id, m, name)` | `robot.play_motion(name, Storage(m))` |
| `request_motion_play(m, name, dt, ms)` | `robot.play_motion(name, storage, at=dt, add_ms=ms)` |
| `request_task_play(m, name)` | `robot.play_task(name, storage)` |
| `request_motion_stop()` / `request_task_stop()` | `robot.stop_playback()` |
| `request_motion_is_playing(); wait(); get_is_playing()` | `robot.is_playing()` |
| `request_filelist(m); wait(); get_FileList(m, i)` | `robot.list_files(storage)` |
| `request_file_delete(m, name)` / `(m)` | `robot.delete_file(name, storage)` / `robot.delete_all_files(storage, confirm=True)` |
| `request_battery(); get_battery_robot()/get_battery_MID()` | `robot.battery().robot_percent / .mid_percent` |
| `request_time(); get_time_sec()` / `request_set_time(dt)` | `robot.get_time()` / `robot.set_time(dt)` |
| `request_remocon()` | `robot.remocon()` |
| `request_heartbeat()` | `robot.heartbeat()` (returns round-trip seconds) |
| `request_auth(id, pw)` | `robot.auth(id, pw)` |
| `set_param(axis, id, dir, up, dn, center, mech, deg)` | `[joints.<name>]` in `genibo.toml` |
| `set_cmd_angle(axis, a)` … `request_move(ms)` | `robot.move({"joint": a, ...}, ms)` |
| `set_cmd(axis, raw)` | `robot.move({...}, ms, raw=True)` |
| `set_cmd_flag_led(axis, g, b, r)` | `robot.move(..., led=Led.GREEN | Led.RED)` |
| `set_cmd_flag_mode(axis, true)` | `robot.jog([JogEntry(v, JogFlag.SPEED_MODE, id)], ms)` |
| `drvsrv(drv, srv)` / `drvsrv(axis, …)` | `robot.torque(on[, joint])` / `robot.torque_mode(Torque.X, joint)` |
| `request_stop(axis)` | `robot.stop_joints([joint])` |
| `request_ems()` | `robot.emergency_stop()` |
| `reset()`, `reset_stop()`, `reset_ems()` | `robot.clear_emergency()` |
| `serial_mpsu_play_headled_buzz/headled/buzz` | `robot.head_led_buzz(led, buzz, mpsu_id=…)` |
| `serial_mpsu_play_motion/task` | `robot.mpsu_play_motion(i, ready=…)` / `robot.mpsu_play_task(t)` |
| `serial_mpsu_write_rom/ram` | `robot.mpsu_write(addr, data, rom=…, mpsu_id=…)` |
| `drbluetooth_mpsu_request_*` | same methods with `direct=True`; `robot.mpsu_read_ram(addr, n, mpsu_id=…)` |
| `send_data(cmd, n, data)` | `robot.send_raw(cmd, data)` |

TCP (`Ojw.CGenibo`): `Connect(ip)` → `GeniboNet(ip).open()`, `Auth` →
`net.auth`, `ActionPlay` → `net.action_play`, `SendData` → `net.send`.

## Behaviour that intentionally differs

The modern API (not the shim) raises instead of sending corrupted frames: play
times above ~2856 ms, names that do not fit, uncalibrated joints, out-of-limit
angles (unless `clip=True`). Queries wait for and return their reply instead of
setting fields read later. `stop()`/`reset()` semantics are made explicit
(`stop_joints`, `emergency_stop`, `clear_emergency`). See `COMPATIBILITY.md`.

## What cannot be migrated automatically

* WinForms UI code: rewrite as a host script, or use the studio.
* `.act` motion and task files: stay on the robot unchanged; the SDK can list,
  fetch, store and play them, but cannot open or convert them (format unknown).
* Generic OpenJigWare features unrelated to Genibo (3D designer, `.dmt` motion
  editor, Dynamixel tools) are out of scope for this SDK.
