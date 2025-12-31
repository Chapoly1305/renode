//
// Copyright (c) 2010-2024 Antmicro
//
// This file is licensed under the MIT License.
// Full license text is available in 'licenses/MIT.txt'.
//
using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;

using Antmicro.Renode.Core;
using Antmicro.Renode.Exceptions;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.Wireless;

namespace Antmicro.Renode.Plugins.BLEBridgePlugin
{
    public class BLEBridgeServer : IExternal, IDisposable
    {
        public BLEBridgeServer(int rxPort = 5000, int txPort = 5001, string txHost = "127.0.0.1")
        {
            this.rxPort = rxPort;
            this.txPort = txPort;
            this.txHost = txHost;

            txSocket = new UdpClient();
            txEndpoint = new IPEndPoint(IPAddress.Parse(txHost), txPort);

            rxSocket = new UdpClient(rxPort);
            rxSocket.Client.ReceiveTimeout = 100; // 100ms timeout for clean shutdown

            isRunning = true;
            receiveThread = new Thread(ReceiveLoop)
            {
                IsBackground = true,
                Name = "BLEBridge-RX"
            };
            receiveThread.Start();

            this.Log(LogLevel.Info, "BLE Bridge Server started. RX port: {0}, TX to {1}:{2}", rxPort, txHost, txPort);
        }

        public void AttachTo(WirelessMedium medium, IRadio radio)
        {
            if(attachedRadio != null)
            {
                throw new RecoverableException("BLE Bridge is already attached to a radio.");
            }

            attachedMedium = medium;
            attachedRadio = radio;
            medium.FrameProcessed += OnFrameProcessed;

            this.Log(LogLevel.Info, "BLE Bridge attached to radio on medium.");
        }

        public void Detach()
        {
            if(attachedMedium != null)
            {
                attachedMedium.FrameProcessed -= OnFrameProcessed;
                attachedMedium = null;
                attachedRadio = null;
                this.Log(LogLevel.Info, "BLE Bridge detached.");
            }
        }

        public void Dispose()
        {
            isRunning = false;
            Detach();

            receiveThread?.Join(500);

            txSocket?.Close();
            rxSocket?.Close();

            this.Log(LogLevel.Info, "BLE Bridge Server disposed.");
        }

        private void OnFrameProcessed(IExternal source, IRadio sender, byte[] frame)
        {
            if(sender != attachedRadio)
            {
                return;
            }

            // Protocol: [Type:1][Channel:1][Len:2 LE][Data:N]
            // Type: 0x01 = TX (Renode -> Python)
            var packet = new byte[4 + frame.Length];
            packet[0] = PacketTypeTX;
            packet[1] = (byte)sender.Channel;
            packet[2] = (byte)(frame.Length & 0xFF);
            packet[3] = (byte)((frame.Length >> 8) & 0xFF);
            Array.Copy(frame, 0, packet, 4, frame.Length);

            try
            {
                txSocket.Send(packet, packet.Length, txEndpoint);
                this.Log(LogLevel.Debug, "TX frame: channel={0}, len={1}", sender.Channel, frame.Length);
            }
            catch(Exception e)
            {
                this.Log(LogLevel.Warning, "Failed to send UDP packet: {0}", e.Message);
            }
        }

        private void ReceiveLoop()
        {
            var remoteEP = new IPEndPoint(IPAddress.Any, 0);

            while(isRunning)
            {
                try
                {
                    var data = rxSocket.Receive(ref remoteEP);
                    ProcessReceivedData(data);
                }
                catch(SocketException e)
                {
                    // Timeout is expected, just continue
                    if(e.SocketErrorCode != SocketError.TimedOut)
                    {
                        this.Log(LogLevel.Warning, "UDP receive error: {0}", e.Message);
                    }
                }
                catch(ObjectDisposedException)
                {
                    // Socket was closed, exit loop
                    break;
                }
            }
        }

        private void ProcessReceivedData(byte[] data)
        {
            if(data.Length < 4)
            {
                this.Log(LogLevel.Warning, "Received packet too short: {0} bytes", data.Length);
                return;
            }

            var packetType = data[0];
            var channel = data[1];
            var length = data[2] | (data[3] << 8);

            if(data.Length < 4 + length)
            {
                this.Log(LogLevel.Warning, "Received packet truncated: expected {0}, got {1}", 4 + length, data.Length);
                return;
            }

            if(packetType != PacketTypeRX)
            {
                this.Log(LogLevel.Debug, "Ignoring packet with type 0x{0:X2}", packetType);
                return;
            }

            var frame = new byte[length];
            Array.Copy(data, 4, frame, 0, length);

            InjectFrame(channel, frame);
        }

        private void InjectFrame(int channel, byte[] frame)
        {
            if(attachedRadio == null)
            {
                this.Log(LogLevel.Warning, "Cannot inject frame: no radio attached");
                return;
            }

            // Set channel and inject frame
            attachedRadio.Channel = channel;
            attachedRadio.ReceiveFrame(frame, null);

            this.Log(LogLevel.Debug, "RX frame injected: channel={0}, len={1}", channel, frame.Length);
        }

        private readonly UdpClient txSocket;
        private readonly UdpClient rxSocket;
        private readonly IPEndPoint txEndpoint;
        private readonly Thread receiveThread;
        private readonly int rxPort;
        private readonly int txPort;
        private readonly string txHost;

        private WirelessMedium attachedMedium;
        private IRadio attachedRadio;
        private volatile bool isRunning;

        private const byte PacketTypeTX = 0x01;
        private const byte PacketTypeRX = 0x02;
    }
}
