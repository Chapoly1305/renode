//
// Copyright (c) 2010-2024 Antmicro
//
// This file is licensed under the MIT License.
// Full license text is available in 'licenses/MIT.txt'.
//
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals.Wireless;

namespace Antmicro.Renode.Plugins.BLEBridgePlugin
{
    public static class EmulationExtensions
    {
        public static void CreateBLEBridgeServer(this Emulation emulation, string name,
            int rxPort = 5000, int txPort = 5001, string txHost = "127.0.0.1")
        {
            var bridge = new BLEBridgeServer(rxPort, txPort, txHost);
            emulation.ExternalsManager.AddExternal(bridge, name);
        }
    }

    public static class BLEBridgeServerExtensions
    {
        public static void AttachTo(this BLEBridgeServer bridge, WirelessMedium medium, IRadio radio)
        {
            bridge.AttachTo(medium, radio);
        }
    }
}
