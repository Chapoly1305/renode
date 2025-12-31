//
// Copyright (c) 2010-2024 Antmicro
//
// This file is licensed under the MIT License.
// Full license text is available in 'licenses/MIT.txt'.
//
using System;

using Antmicro.Renode.UserInterface;

namespace Antmicro.Renode.Plugins.BLEBridgePlugin
{
    [Plugin(Name = "BLE Bridge Plugin", Version = "1.0", Description = "Provides BLE bridge to host BlueZ via UDP.", Vendor = "Antmicro")]
    public sealed class BLEBridgePlugin : IDisposable
    {
        public BLEBridgePlugin(Monitor _)
        {
        }

        public void Dispose()
        {
        }
    }
}
