// Golden-output generator for the Genibo Python SDK.
//
// Every scenario calls the ORIGINAL OpenJigWare methods (Ojw.CGenibo_Bt /
// Ojw.CGenibo) and records the bytes they write. Receive scenarios feed frames
// produced by the legacy send path back into the legacy receive parser and
// record what its public getters report.
//
// Usage: dotnet run -- <output.json>
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Ports;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using OpenJigWare;

static class Program
{
    static readonly List<Dictionary<string, object>> Tx = new List<Dictionary<string, object>>();
    static readonly List<Dictionary<string, object>> Rx = new List<Dictionary<string, object>>();
    static readonly List<Dictionary<string, object>> Tables = new List<Dictionary<string, object>>();
    static string TmpDir;

    static string Hex(byte[] b) => string.Concat(b.Select(x => x.ToString("x2")));

    static Ojw.CGenibo_Bt NewBt(int robotId)
    {
        SerialPort.ResetAll();
        var bt = new Ojw.CGenibo_Bt();
        if (!bt.connect(5, 115200)) throw new Exception("stub connect failed");
        bt.drbluetooth_set_id(robotId);
        SerialPort.ResetAll();
        return bt;
    }

    static List<string> TakeWrites()
    {
        lock (SerialPort.Gate)
        {
            var w = SerialPort.Writes.Select(Hex).ToList();
            SerialPort.Writes.Clear();
            return w;
        }
    }

    // Records one transmit scenario: the call text, structured args for the
    // data-driven Python test, and the exact bytes written (one entry per Write()).
    static void Bt(string id, string call, Dictionary<string, object> args, Action<Ojw.CGenibo_Bt> act, int robotId = 0x20, Action<Ojw.CGenibo_Bt> setup = null)
    {
        var bt = NewBt(robotId);
        setup?.Invoke(bt);
        TakeWrites();
        act(bt);
        var writes = TakeWrites();
        bt.disconnect();
        Tx.Add(new Dictionary<string, object> {
            ["id"] = id, ["class"] = "CGenibo_Bt", ["robot_id"] = robotId,
            ["call"] = call, ["args"] = args ?? new Dictionary<string, object>(), ["writes"] = writes,
        });
    }

    static Dictionary<string, object> A(params object[] kv)
    {
        var d = new Dictionary<string, object>();
        for (int i = 0; i < kv.Length; i += 2) d[(string)kv[i]] = kv[i + 1];
        return d;
    }

    // The canonical Genibo servo parameters used by the scenarios. These are
    // test inputs, not claims about real Genibo calibration.
    static void Params(Ojw.CGenibo_Bt bt)
    {
        bt.set_param(0, 0, 0, 0f, 0f, 512, 1024f, 333.3f);
        bt.set_param(1, 1, 1, 0f, 0f, 512, 1024f, 333.3f);
        bt.set_param(2, 15, 0, 0f, 0f, 512, 1024f, 333.3f);
        bt.set_param(3, 7, 0, 60f, -45f, 500, 1024f, 333.3f);
    }

    static byte[] Frame(int robotId, int cmd, byte[] data)
    {
        // Frame bytes produced by the legacy *send* path (send_data).
        var bt = NewBt(robotId);
        bt.send_data(cmd, data?.Length ?? 0, data);
        var w = SerialPort.Writes.ToList();
        SerialPort.ResetAll();
        bt.disconnect();
        return w.SelectMany(x => x).ToArray();
    }

    static byte[] MpsuFrame(int mpsuId, int cmd, byte[] data)
    {
        var bt = NewBt(mpsuId);
        bt.drbluetooth_mpsu_send_data(cmd, data.Length, data);
        var w = SerialPort.Writes.ToList();
        SerialPort.ResetAll();
        bt.disconnect();
        return w.SelectMany(x => x).ToArray();
    }

    static byte[] Name51(string s)
    {
        var b = new byte[51];
        var src = Encoding.ASCII.GetBytes(s);
        Array.Copy(src, b, Math.Min(src.Length, 51));
        return b;
    }

    static void RxCase(string id, int robotId, byte[] input, Func<Ojw.CGenibo_Bt, Dictionary<string, object>> read, string note = null)
    {
        var bt = NewBt(robotId);
        SerialPort.Inject(input);
        for (int i = 0; i < 300 && SerialPort.LastOpened.BytesToRead > 0; i++) Thread.Sleep(5);
        Thread.Sleep(150);
        var result = read(bt);
        bt.disconnect();
        Thread.Sleep(30);
        Rx.Add(new Dictionary<string, object> {
            ["id"] = id, ["robot_id"] = robotId, ["input"] = Hex(input), ["result"] = result, ["note"] = note ?? "",
        });
    }

    static object Invoke(object o, string method, params object[] args)
    {
        var m = o.GetType().GetMethod(method, BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Public);
        return m.Invoke(o, args);
    }

    static void Main(string[] argv)
    {
        string outPath = argv.Length > 0 ? argv[0] : "golden.json";
        TmpDir = Path.Combine(Path.GetTempPath(), "genibo-golden-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(TmpDir);

        string name50 = new string('a', 46) + ".act";          // 50 bytes
        string name51 = new string('b', 47) + ".act";          // 51 bytes
        string name60 = new string('c', 56) + ".act";          // 60 bytes

        // ---- Connection (Ex20 uses connect(5, 115200)) ----
        {
            SerialPort.ResetAll();
            var bt = new Ojw.CGenibo_Bt();
            bt.connect(5, 115200);
            var sp = SerialPort.LastOpened;
            Tx.Add(new Dictionary<string, object> {
                ["id"] = "bt.connect.ex20", ["class"] = "CGenibo_Bt", ["robot_id"] = 0, ["call"] = "connect(5, 115200)",
                ["args"] = A("port", sp.PortName, "baudrate", sp.BaudRate, "parity", sp.Parity.ToString(), "data_bits", sp.DataBits, "stop_bits", sp.StopBits.ToString()),
                ["writes"] = TakeWrites(),
            });
            bt.disconnect();
        }

        // ---- Motion / task playback (files stored on the robot) ----
        Bt("bt.ex20.play", "Cmd_Bluetooth_Play(0x20, 0, \"7.act\")", A("robot", 0x20, "storage", 0, "name", "7.act"),
            bt => bt.Cmd_Bluetooth_Play(0x20, 0, "7.act"), robotId: 0);
        Bt("bt.ex20.play.broadcast", "Cmd_Bluetooth_Play(0xfe, 1, \"walk.act\")", A("robot", 0xfe, "storage", 1, "name", "walk.act"),
            bt => bt.Cmd_Bluetooth_Play(0xfe, 1, "walk.act"), robotId: 0);
        foreach (var (tag, nm) in new[] { ("short", "a"), ("50", name50), ("51", name51), ("60", name60) })
            Bt("bt.motion_play_now." + tag, $"request_motion_play_now(1, \"{nm}\")", A("storage", 1, "name", nm), bt => bt.request_motion_play_now(1, nm));
        {
            var when = new DateTime(2024, 5, 6, 7, 8, 9, DateTimeKind.Utc).AddTicks(1234567);
            Bt("bt.motion_play_at", "request_motion_play(0, \"dance.act\", 2024-05-06T07:08:09.1234567Z, 250)",
                A("storage", 0, "name", "dance.act", "utc", "2024-05-06T07:08:09.1234567Z", "add_ms", 250),
                bt => bt.request_motion_play(0, "dance.act", when, 250));
        }
        Bt("bt.action_play_alias", "Action_Play(0, \"7.act\")", A("storage", 0, "name", "7.act"), bt => bt.Action_Play(0, "7.act"));
        Bt("bt.motion_stop", "request_motion_stop()", null, bt => bt.request_motion_stop());
        Bt("bt.motion_is_playing", "request_motion_is_playing()", null, bt => bt.request_motion_is_playing());
        Bt("bt.task_play", "request_task_play(0, \"patrol.tsk\")", A("storage", 0, "name", "patrol.tsk"), bt => bt.request_task_play(0, "patrol.tsk"));
        Bt("bt.task_play.51", $"request_task_play(1, \"{name51}\")", A("storage", 1, "name", name51), bt => bt.request_task_play(1, name51));
        Bt("bt.task_stop", "request_task_stop()", null, bt => bt.request_task_stop());
        Bt("bt.task_is_playing", "request_task_is_playing()", null, bt => bt.request_task_is_playing());

        // ---- Robot file system ----
        Bt("bt.filelist.master", "request_filelist(0)", A("storage", 0), bt => bt.request_filelist(0));
        Bt("bt.filelist.user", "request_filelist(1)", A("storage", 1), bt => bt.request_filelist(1));
        Bt("bt.file_delete", "request_file_delete(1, \"old.act\")", A("storage", 1, "name", "old.act"), bt => bt.request_file_delete(1, "old.act"));
        Bt("bt.file_delete_all", "request_file_delete(0)", A("storage", 0), bt => bt.request_file_delete(0));
        Bt("bt.file_fetch", "request_file_upload(0, \"7.act\")  [robot -> PC]", A("storage", 0, "name", "7.act"), bt => bt.request_file_upload(0, "7.act"));
        {
            var content = Enumerable.Range(0, 300).Select(i => (byte)(i % 256)).ToArray();
            var path = Path.Combine(TmpDir, "payload.bin");
            File.WriteAllBytes(path, content);
            Bt("bt.file_send", "request_file_download(1, payload(300 bytes), \"my.act\")  [PC -> robot]",
                A("storage", 1, "name", "my.act", "content_hex", Hex(content)), bt => bt.request_file_download(1, path, "my.act"));
            Bt("bt.file_send.indexed", "request_file_download(0, 2, 5, payload(300 bytes), \"part.act\")",
                A("storage", 0, "name", "part.act", "index", 2, "total", 5, "content_hex", Hex(content)), bt => bt.request_file_download(0, 2, 5, path, "part.act"));
            Bt("bt.file_send.name60", $"request_file_download(1, payload, \"{name60}\")",
                A("storage", 1, "name", name60, "content_hex", Hex(content)), bt => bt.request_file_download(1, path, name60));
        }

        // ---- Status queries ----
        {
            var when = new DateTime(2023, 11, 3, 12, 34, 56, DateTimeKind.Utc).AddTicks(7654321);
            Bt("bt.set_time", "request_set_time(2023-11-03T12:34:56.7654321Z)", A("utc", "2023-11-03T12:34:56.7654321Z"), bt => bt.request_set_time(when));
        }
        Bt("bt.get_time", "request_time()", null, bt => bt.request_time());
        Bt("bt.battery", "request_battery()", null, bt => bt.request_battery());
        Bt("bt.charge", "request_charge()", null, bt => bt.request_charge());
        Bt("bt.remocon", "request_remocon()", null, bt => bt.request_remocon());
        Bt("bt.heartbeat", "request_heartbeat()", null, bt => bt.request_heartbeat());
        Bt("bt.auth", "request_auth(\"geniibo\", \"1234\")", A("user", "geniibo", "password", "1234"), bt => bt.request_auth("geniibo", "1234"));
        Bt("bt.auth.long", "request_auth(30 chars, 25 chars)", A("user", new string('u', 30), "password", new string('p', 25)),
            bt => bt.request_auth(new string('u', 30), new string('p', 25)));
        Bt("bt.send_command", "send_command(0x55)", A("cmd", 0x55, "data_hex", ""), bt => bt.send_command(0x55));
        {
            var big = Enumerable.Range(0, 300).Select(i => (byte)((i * 7) & 0xff)).ToArray();
            Bt("bt.send_data.300", "send_data(0x32, 300, data)", A("cmd", 0x32, "data_hex", Hex(big)), bt => bt.send_data(0x32, big.Length, big));
        }

        // ---- Servo control (HerkuleX-style packets tunnelled in cmd 0x81) ----
        Bt("bt.servo.move.multi", "set_cmd_angle(0,10) set_cmd_angle(1,-25.5) set_cmd(2,600) request_move(1000)",
            A("time_ms", 1000, "targets", new object[] { A("axis", 0, "angle", 10.0), A("axis", 1, "angle", -25.5), A("axis", 2, "raw", 600) }),
            bt => { bt.set_cmd_angle(0, 10.0f); bt.set_cmd_angle(1, -25.5f); bt.set_cmd(2, 600); bt.request_move(1000); }, setup: Params);
        Bt("bt.servo.move.flags", "led(0: g,r) speed-mode(1) angle(1)=100 request_move(500)",
            A("time_ms", 500),
            bt => {
                bt.set_cmd_angle(0, 5.0f); bt.set_cmd_flag_led(0, true, false, true);
                bt.set_cmd_flag_mode(1, true); bt.set_cmd_angle(1, 100.0f); bt.set_cmd_flag_led_blue(1, true);
                bt.request_move(500);
            }, setup: Params);
        Bt("bt.servo.move.single", "set_cmd_angle(2, 30) request_move(2, 300)", A("axis", 2, "angle", 30.0, "time_ms", 300),
            bt => { bt.set_cmd_angle(2, 30.0f); bt.request_move(2, 300); }, setup: Params);
        Bt("bt.servo.move.limit_clip", "axis3 limits(+60,-45) set_cmd_angle(3, 90) request_move(200)", A("axis", 3, "angle", 90.0, "time_ms", 200),
            bt => { bt.set_cmd_angle(3, 90.0f); bt.request_move(200); }, setup: Params);
        Bt("bt.servo.move.empty", "request_move(1000) with no targets", A("time_ms", 1000), bt => bt.request_move(1000), setup: Params);
        Bt("bt.servo.torque.on", "drvsrv(true, true)", A("driver", true, "servo", true), bt => bt.drvsrv(true, true));
        Bt("bt.servo.torque.off", "drvsrv(false, false)", A("driver", false, "servo", false), bt => bt.drvsrv(false, false));
        Bt("bt.servo.torque.brake", "drvsrv(true, false)", A("driver", true, "servo", false), bt => bt.drvsrv(true, false));
        Bt("bt.servo.torque.axis", "drvsrv(3, true, true) [axis 3 -> id 7]", A("axis", 3, "driver", true, "servo", true), bt => bt.drvsrv(3, true, true), setup: Params);
        Bt("bt.servo.stop", "request_stop()", null, bt => bt.request_stop(), setup: Params);
        Bt("bt.servo.ems", "request_ems()", null, bt => bt.request_ems(), setup: Params);
        Bt("bt.servo.reset", "reset()", null, bt => bt.reset(), setup: Params);
        Bt("bt.servo.move_after_stop", "request_stop(); set_cmd_angle(0,10); request_move(1000)", null,
            bt => { bt.request_stop(); bt.set_cmd_angle(0, 10f); bt.request_move(1000); }, setup: Params);
        Bt("bt.servo.stop_axis", "set_cmd_angle(0,10); request_stop(0)", null,
            bt => { bt.set_cmd_angle(0, 10f); bt.request_stop(0); }, setup: Params);

        // ---- MPSU commands tunnelled in cmd 0x81 ----
        Bt("bt.mpsu.headled_buzz", "serial_mpsu_play_headled_buzz(0xfe, 3, 5)", A("mpsu", 0xfe, "led", 3, "buzz", 5), bt => bt.serial_mpsu_play_headled_buzz(0xfe, 3, 5));
        Bt("bt.mpsu.headled", "serial_mpsu_play_headled(0x40, 7)", A("mpsu", 0x40, "led", 7), bt => bt.serial_mpsu_play_headled(0x40, 7));
        Bt("bt.mpsu.buzz", "serial_mpsu_play_buzz(0xfe, 9)", A("mpsu", 0xfe, "buzz", 9), bt => bt.serial_mpsu_play_buzz(0xfe, 9));
        Bt("bt.mpsu.task", "serial_mpsu_play_task(0xfe, 1)", A("mpsu", 0xfe, "task", 1), bt => bt.serial_mpsu_play_task(0xfe, 1));
        Bt("bt.mpsu.motion", "serial_mpsu_play_motion(0xfe, 12, true)", A("mpsu", 0xfe, "motion", 12, "ready", true), bt => bt.serial_mpsu_play_motion(0xfe, 12, true));
        Bt("bt.mpsu.rom1", "serial_mpsu_write_rom(0x40, 7, 0x41)", A("mpsu", 0x40, "address", 7, "data_hex", "41"), bt => bt.serial_mpsu_write_rom(0x40, 7, (byte)0x41));
        Bt("bt.mpsu.rom3", "serial_mpsu_write_rom(0x40, 10, {1,2,3})", A("mpsu", 0x40, "address", 10, "data_hex", "010203"), bt => bt.serial_mpsu_write_rom(0x40, 10, new byte[] { 1, 2, 3 }));
        Bt("bt.mpsu.ram1", "serial_mpsu_write_ram(0x40, 0, 0x41)", A("mpsu", 0x40, "address", 0, "data_hex", "41"), bt => bt.serial_mpsu_write_ram(0x40, 0, (byte)0x41));
        Bt("bt.mpsu.ram2", "serial_mpsu_write_ram(0x40, 20, {9,8})", A("mpsu", 0x40, "address", 20, "data_hex", "0908"), bt => bt.serial_mpsu_write_ram(0x40, 20, new byte[] { 9, 8 }));

        // ---- MPSU commands sent directly (no 0x81 wrapper) ----
        Bt("bt.mpsu_direct.battery", "drbluetooth_mpsu_request_battery()", null, bt => bt.drbluetooth_mpsu_request_battery(), robotId: 0x40);
        Bt("bt.mpsu_direct.motion", "drbluetooth_mpsu_request_motion_play(5, false)", A("motion", 5, "ready", false), bt => bt.drbluetooth_mpsu_request_motion_play(5, false), robotId: 0x40);
        Bt("bt.mpsu_direct.motion_ready", "drbluetooth_mpsu_request_motion_play(5, true)", A("motion", 5, "ready", true), bt => bt.drbluetooth_mpsu_request_motion_play(5, true), robotId: 0x40);
        Bt("bt.mpsu_direct.motion_stop", "drbluetooth_mpsu_request_motion_stop()", null, bt => bt.drbluetooth_mpsu_request_motion_stop(), robotId: 0x40);
        Bt("bt.mpsu_direct.motion_reserve", "drbluetooth_mpsu_request_motion_play_reserve(5, false, 1500)", A("motion", 5, "ready", false, "delay_ms", 1500),
            bt => bt.drbluetooth_mpsu_request_motion_play_reserve(5, false, 1500), robotId: 0x40);
        Bt("bt.mpsu_direct.play_mpsu", "Cmd_Bluetooth_Play_Mpsu(0xfe, 3)", A("robot", 0xfe, "motion", 3), bt => bt.Cmd_Bluetooth_Play_Mpsu(0xfe, 3), robotId: 0);
        Bt("bt.mpsu_direct.motor_move", "set_cmd_angle(0,10) set_cmd_angle(1,-25.5) drbluetooth_mpsu_motor_request_move(800)",
            A("time_ms", 800, "targets", new object[] { A("axis", 0, "angle", 10.0), A("axis", 1, "angle", -25.5) }),
            bt => { bt.set_cmd_angle(0, 10f); bt.set_cmd_angle(1, -25.5f); bt.drbluetooth_mpsu_motor_request_move(800); }, robotId: 0x40, setup: Params);

        // ---- Numeric conversion tables (private helpers via reflection) ----
        {
            var bt = NewBt(0x20);
            var times = new[] { 0, 1, 5, 6, 11, 12, 17, 50, 100, 112, 168, 280, 500, 1000, 1500, 2000, 2850, 2856, 2862, 3000, 5000 };
            Tables.Add(new Dictionary<string, object> {
                ["id"] = "calc_time_ms", ["inputs"] = times, ["outputs"] = times.Select(t => (int)Invoke(bt, "CalcTime_ms", t)).ToArray(),
            });
            var paramSets = new[] {
                (center: 512, mech: 1024f, deg: 333.3f, dir: 0),
                (center: 512, mech: 1024f, deg: 333.3f, dir: 1),
                (center: 0, mech: 2f, deg: 4f, dir: 0),        // exact .5 midpoints: banker's rounding
                (center: 2048, mech: 4096f, deg: 360f, dir: 1),
            };
            var angles = new[] { -180f, -90f, -45.5f, -25.5f, -10f, -1f, -0.5f, 0f, 0.1f, 0.5f, 1f, 1.5f, 2.5f, 3f, 5f, 10f, 30f, 33.33f, 45f, 90f, 120.7f, 150f, 166.65f, 180f };
            int k = 0;
            foreach (var p in paramSets)
            {
                bt.set_param(0, 1, p.dir, 0f, 0f, p.center, p.mech, p.deg);
                Tables.Add(new Dictionary<string, object> {
                    ["id"] = "angle_to_raw." + k, ["center"] = p.center, ["mech"] = p.mech, ["degree"] = p.deg, ["dir"] = p.dir,
                    ["inputs"] = angles, ["outputs"] = angles.Select(a => (int)Invoke(bt, "CalcAngle2Evd", 0, a)).ToArray(),
                });
                var raws = new[] { 0, 1, 100, 255, 256, 511, 512, 513, 700, 1023, 2048, 4095 };
                Tables.Add(new Dictionary<string, object> {
                    ["id"] = "raw_to_angle." + k, ["center"] = p.center, ["mech"] = p.mech, ["degree"] = p.deg, ["dir"] = p.dir,
                    ["inputs"] = raws, ["outputs"] = raws.Select(r => (double)(float)Invoke(bt, "CalcEvd2Angle", 0, r)).ToArray(),
                });
                k++;
            }
            // Limit clipping through the public set_cmd_angle/get_cmd path.
            bt.set_param(0, 1, 0, 60f, -45f, 512, 1024f, 333.3f);
            var clipAngles = new[] { -90f, -45f, -44.9f, 0f, 59.9f, 60f, 61f, 120f };
            Tables.Add(new Dictionary<string, object> {
                ["id"] = "set_cmd_angle.clip", ["center"] = 512, ["mech"] = 1024f, ["degree"] = 333.3f, ["dir"] = 0, ["limit_up"] = 60f, ["limit_dn"] = -45f,
                ["inputs"] = clipAngles, ["outputs"] = clipAngles.Select(a => { bt.set_cmd_angle(0, a); return bt.get_cmd(0); }).ToArray(),
            });
            bt.set_param(0, 1, 1, 60f, -45f, 512, 1024f, 333.3f);
            Tables.Add(new Dictionary<string, object> {
                ["id"] = "set_cmd_angle.clip.dir1", ["center"] = 512, ["mech"] = 1024f, ["degree"] = 333.3f, ["dir"] = 1, ["limit_up"] = 60f, ["limit_dn"] = -45f,
                ["inputs"] = clipAngles, ["outputs"] = clipAngles.Select(a => { bt.set_cmd_angle(0, a); return bt.get_cmd(0); }).ToArray(),
            });
            bt.disconnect();
        }

        // ---- Receive parser behaviour ----
        RxCase("rx.battery", 0x20, Frame(0x20, 0x63, new byte[] { 0x55, 0x4b }),
            bt => A("battery_robot", bt.get_battery_robot(), "battery_mid", bt.get_battery_MID(), "status", bt.packet_status(), "seq", bt.seq()));
        RxCase("rx.playing", 0x20, Frame(0x20, 0x41, new byte[] { 1 }),
            bt => A("is_playing", bt.get_is_playing(), "seq", bt.seq()));
        RxCase("rx.time", 0x20, Frame(0x20, 0x62, new byte[] { 0x65, 0x44, 0xe8, 0x50, 0x00, 0x0b, 0x6f, 0x5d }),
            bt => A("time_sec", bt.get_time_sec(), "time_usec", bt.get_time_usec(), "seq", bt.seq()));
        RxCase("rx.remocon", 0x20, Frame(0x20, 0x65, new byte[] { 3, 0x21 }),
            bt => A("remocon_length", bt.get_remocon_length(), "remocon_data", bt.get_remocon_data(), "seq", bt.seq()));
        RxCase("rx.filelist.master", 0x20, Frame(0x20, 0x31, new byte[] { 0, 0, 2 }.Concat(Name51("7.act")).Concat(Name51("walk.act")).ToArray()),
            bt => A("count", bt.get_FileList_Count(0), "names", Enumerable.Range(0, bt.get_FileList_Count(0)).Select(i => bt.get_FileList(0, i).TrimEnd('\0')).ToArray(), "seq", bt.seq()));
        RxCase("rx.filelist.user", 0x20, Frame(0x20, 0x31, new byte[] { 1, 0, 1 }.Concat(Name51("mine.act")).ToArray()),
            bt => A("count", bt.get_FileList_Count(1), "names", Enumerable.Range(0, bt.get_FileList_Count(1)).Select(i => bt.get_FileList(1, i).TrimEnd('\0')).ToArray(), "seq", bt.seq()));
        RxCase("rx.status", 0x20, Frame(0x20, 0xf0, new byte[] { 0x40, 0x02 }),
            bt => A("status", bt.packet_status(), "seq", bt.seq()));
        RxCase("rx.broadcast_id", 0x20, Frame(0xfe, 0x63, new byte[] { 10, 20 }),
            bt => A("battery_robot", bt.get_battery_robot(), "battery_mid", bt.get_battery_MID(), "seq", bt.seq()));
        RxCase("rx.other_id_ignored", 0x20, Frame(0x21, 0x63, new byte[] { 10, 20 }),
            bt => A("battery_robot", bt.get_battery_robot(), "battery_mid", bt.get_battery_MID(), "seq", bt.seq()));
        {
            var f = Frame(0x20, 0x63, new byte[] { 0x55, 0x4b });
            f[f.Length - 1] ^= 0x01;
            RxCase("rx.bad_checksum", 0x20, f, bt => A("battery_robot", bt.get_battery_robot(), "battery_mid", bt.get_battery_MID(), "seq", bt.seq()));
        }
        RxCase("rx.zero_length_then_battery", 0x20,
            Frame(0x20, 0xf1, new byte[0]).Concat(Frame(0x20, 0x63, new byte[] { 0x55, 0x4b })).ToArray(),
            bt => A("battery_robot", bt.get_battery_robot(), "battery_mid", bt.get_battery_MID(), "status", bt.packet_status(), "seq", bt.seq()),
            note: "Legacy parser consumes one extra byte after a zero-length frame; documents the quirk.");
        RxCase("rx.two_frames", 0x20,
            Frame(0x20, 0x41, new byte[] { 1 }).Concat(Frame(0x20, 0x63, new byte[] { 0x30, 0x31 })).ToArray(),
            bt => A("is_playing", bt.get_is_playing(), "battery_robot", bt.get_battery_robot(), "battery_mid", bt.get_battery_MID(), "seq", bt.seq()));
        RxCase("rx.mpsu_ram", 0x40, MpsuFrame(0x40, 0x54, new byte[] { 70, 3, 0x11, 0x22, 0x33, 0x00, 0x00 }),
            bt => A("ram_70_76", bt.drbluetooth_mpsu_get_data_ram().Skip(70).Take(7).Select(x => (int)x).ToArray(), "seq", bt.seq()),
            note: "Legacy parser copies the payload starting at the address byte, not at the data.");

        // ---- TCP client (Ojw.CGenibo, ports 6100..6105) ----
        {
            void Tcp(string id, string call, Dictionary<string, object> args, Action<Ojw.CGenibo> act)
            {
                var g = new Ojw.CGenibo();
                var socks = (Ojw.CSocket[])typeof(Ojw.CGenibo).GetField("OjwSock", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(g);
                lock (Ojw.CSocket.Sent) Ojw.CSocket.Sent.Clear();
                act(g);
                List<object> writes;
                lock (Ojw.CSocket.Sent)
                    writes = Ojw.CSocket.Sent.Select(kv => (object)A("channel", Array.IndexOf(socks, kv.Key), "hex", Hex(kv.Value))).ToList();
                Tx.Add(new Dictionary<string, object> {
                    ["id"] = id, ["class"] = "CGenibo", ["robot_id"] = -1, ["call"] = call, ["args"] = args ?? new Dictionary<string, object>(), ["writes"] = writes,
                });
            }
            Tcp("tcp.auth", "Auth(\"geniibo\", \"1234\")", A("user", "geniibo", "password", "1234"), g => g.Auth("geniibo", "1234"));
            Tcp("tcp.action_play.master", "ActionPlay(true, \"7.act\", false, false, 100)", A("master", true, "name", "7.act", "sound", false, "emoticon", false, "speed", 100),
                g => g.ActionPlay(true, "7.act", false, false, 100));
            Tcp("tcp.action_play.user", "ActionPlay(false, \"walk.act\", true, true, 50)", A("master", false, "name", "walk.act", "sound", true, "emoticon", true, "speed", 50),
                g => g.ActionPlay(false, "walk.act", true, true, 50));
            Tcp("tcp.action_play.long", "ActionPlay(true, \"abcdefghijklmnopqrst.act\", true, false, 255)", A("master", true, "name", "abcdefghijklmnopqrst.act", "sound", true, "emoticon", false, "speed", 255),
                g => g.ActionPlay(true, "abcdefghijklmnopqrst.act", true, false, 255));
            Tcp("tcp.send_data", "SendData(4, 0x81, 0, [])", A("channel", 4, "cmd", 0x81, "data_hex", ""), g => g.SendData(4, 0x81, 0, new byte[0]));
        }

        // bin/<Configuration>/<tfm>/ -> compat/csharp-reference -> compat/legacy
        string src = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../legacy/OpenJigWare/COjw_25_Genibo.cs"));
        string sha = File.Exists(src) ? Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(src))).ToLowerInvariant() : "";
        var doc = new Dictionary<string, object> {
            ["generator"] = "genibo-sdk/compat/csharp-reference (dotnet " + Environment.Version + ")",
            ["source_file"] = "compat/legacy/OpenJigWare/COjw_25_Genibo.cs",
            ["source_sha256"] = sha,
            ["notes"] = "Bytes recorded from the unmodified legacy implementation. Encoding.Default is UTF-8 on .NET 6; scenarios use ASCII names only.",
            ["tx"] = Tx, ["rx"] = Rx, ["tables"] = Tables,
        };
        File.WriteAllText(outPath, JsonSerializer.Serialize(doc, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine($"wrote {outPath}: {Tx.Count} tx, {Rx.Count} rx, {Tables.Count} tables");
        Directory.Delete(TmpDir, true);
        Environment.Exit(0);
    }
}
