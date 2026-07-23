import struct
from unicorn import *
from unicorn.arm_const import *
BIN="/home/chen/aliro-renode/matter/aliro-u400/fuzzware/firmware/U400.bin"
code=open(BIN,"rb").read()
uc=Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
# enable VFP/FPU so FP instructions don't raise UC_ERR_EXCEPTION
try:
    uc.reg_write(UC_ARM_REG_C1_C0_2, 0x00f00000)
    uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
    print("VFP enabled")
except Exception as e:
    print("VFP enable failed:", e)
uc.mem_map(0x08000000, 0x00800000); uc.mem_write(0x08006000, code)
uc.mem_map(0x00000000, 0x00100000); uc.mem_map(0x0FE00000, 0x00100000)
uc.mem_map(0x20000000, 0x00040000)
uc.mem_map(0x40000000, 0x20000000); uc.mem_map(0xA0000000, 0x20000000); uc.mem_map(0xE0000000, 0x10000000)
last_pc=[0]; n=[0]
def hc(uc,addr,size,ud):
    last_pc[0]=addr; n[0]+=1
uc.hook_add(UC_HOOK_CODE, hc)
def hu(uc,access,addr,size,value,ud):
    print("UNMAPPED 0x%08x pc=0x%08x"%(addr,last_pc[0])); return False
uc.hook_add(UC_HOOK_MEM_UNMAPPED, hu)
sp=struct.unpack_from("<I",code,0)[0]; pc=struct.unpack_from("<I",code,4)[0]
uc.reg_write(UC_ARM_REG_SP, sp)
try:
    uc.emu_start(pc|1, 0, 0, 5000000)
    print("clean return last_pc=0x%08x blocks=%d"%(last_pc[0],n[0]))
    tbl=uc.mem_read(0x20003288,16); print("ctor table:", " ".join("%08x"%struct.unpack_from("<I",tbl,i)[0] for i in range(0,16,4)))
except UcError as e:
    print("UcError: %s last_pc=0x%08x blocks=%d"%(e,last_pc[0],n[0]))
    tbl=uc.mem_read(0x20003288,16); print("ctor table:", " ".join("%08x"%struct.unpack_from("<I",tbl,i)[0] for i in range(0,16,4)))
