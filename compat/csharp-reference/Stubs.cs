// Capture stubs for the types COjw_25_Genibo.cs depends on.
//
// Only I/O endpoints are replaced: SerialPort and CSocket record every byte the
// legacy code writes, and let the harness inject bytes for the receive parser.
// No packet-building logic lives here; all of it stays in the original file.
using System;
using System.Collections.Generic;
using System.Threading;

namespace System.IO.Ports
{
    public enum Parity { None = 0, Odd = 1, Even = 2, Mark = 3, Space = 4 }
    public enum StopBits { None = 0, One = 1, Two = 2, OnePointFive = 3 }

    public class SerialPort
    {
        public static readonly List<byte[]> Writes = new List<byte[]>();
        public static readonly object Gate = new object();
        private static readonly Queue<byte> Incoming = new Queue<byte>();

        public string PortName { get; set; }
        public int BaudRate { get; set; }
        public Parity Parity { get; set; }
        public int DataBits { get; set; }
        public StopBits StopBits { get; set; }
        public int ReceivedBytesThreshold { get; set; }
        public bool IsOpen { get; private set; }

        public static SerialPort LastOpened;

        public void Open() { IsOpen = true; LastOpened = this; }
        public void Close() { IsOpen = false; }

        public void Write(byte[] buffer, int offset, int count)
        {
            byte[] chunk = new byte[count];
            Array.Copy(buffer, offset, chunk, 0, count);
            lock (Gate) Writes.Add(chunk);
        }

        public int BytesToRead { get { lock (Gate) return Incoming.Count; } }

        public int ReadByte()
        {
            // Mirrors a blocking read: the legacy parser only calls this after
            // BytesToRead > 0, or inside get_bytes() for a declared length.
            for (int spin = 0; spin < 2000; spin++)
            {
                lock (Gate) { if (Incoming.Count > 0) return Incoming.Dequeue(); }
                Thread.Sleep(1);
            }
            throw new TimeoutException("stub serial read timeout");
        }

        public static void Inject(byte[] data)
        {
            lock (Gate) foreach (byte b in data) Incoming.Enqueue(b);
        }

        public static void ResetAll()
        {
            lock (Gate) { Writes.Clear(); Incoming.Clear(); }
        }
    }
}

namespace System.Windows.Forms
{
    public static class Application
    {
        public static void DoEvents() { Thread.Sleep(1); }
    }

    public static class MessageBox
    {
        public static readonly List<string> Shown = new List<string>();
        public static int Show(string text) { lock (Shown) Shown.Add(text); return 0; }
    }
}

namespace OpenJigWare
{
    partial class Ojw
    {
        public class CTimer
        {
            private DateTime m_dt = DateTime.Now;
            public void Set() { m_dt = DateTime.Now; }
            public long Get() { return (long)(DateTime.Now - m_dt).TotalMilliseconds; }
        }

        public class CConvert
        {
            public static string IntToHex(int nValue) { return nValue.ToString("X2"); }
            public static string IntToStr(int nValue) { return nValue.ToString(); }
        }

        public class CMessage
        {
            public static readonly List<string> Log = new List<string>();
            public static void Write(string strMessage, params object[] args) { lock (Log) Log.Add(SafeFormat(strMessage, args)); }
            public static void Write_Error(string strMessage, params object[] args) { lock (Log) Log.Add("ERR " + SafeFormat(strMessage, args)); }
            private static string SafeFormat(string s, object[] args)
            {
                try { return (args == null || args.Length == 0) ? s : string.Format(s, args); }
                catch { return s; }
            }
        }

        // TCP socket stand-in for Ojw.CGenibo (ports 6100..6105).
        public class CSocket
        {
            public static readonly List<KeyValuePair<CSocket, byte[]>> Sent = new List<KeyValuePair<CSocket, byte[]>>();
            public bool m_bConnect = false;
            public int Port = -1;

            public bool Connect(string strIp, int nPort) { Port = nPort; m_bConnect = true; return true; }
            public bool DisConnect() { m_bConnect = false; return true; }
            public bool Send(byte[] byteData)
            {
                byte[] copy = new byte[byteData.Length];
                Array.Copy(byteData, copy, byteData.Length);
                lock (Sent) Sent.Add(new KeyValuePair<CSocket, byte[]>(this, copy));
                return true;
            }
            public byte GetByte() { m_bConnect = false; return 0; }
            public int GetInt32() { m_bConnect = false; return 0; }
            public byte[] GetBytes(int nSize) { m_bConnect = false; return new byte[nSize]; }
        }
    }
}
