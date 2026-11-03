"""Descriptor-level checks for the SHA-256 defects reported in upstream PR #2.

Uses generated inputs with equivalent update layouts, not the original SDK
firmware probe. Importing the helpers also executes the upstream known-answer
suite. Run from the repository root with test_lib/pyrenode3 configured.
"""
import hashlib
import json
from pathlib import Path
import runpy


def main():
    helpers = runpy.run_path(str(Path(__file__).with_name("SYMCRYPTO_1.py")))
    pack = helpers["pack_job"]
    cfg = helpers["hash_cfg_word"]
    mode = helpers["HASH_MODE_SHA256"]
    read = helpers["bytes_to_hex"]
    reset = helpers["reset_segment_index"]
    config_tag = helpers["TAG_HASH_CFG"]
    message_tag = helpers["TAG_HASH_MSG"]
    state_tag = message_tag | (1 << 6)

    def operation(data, state=None, final=False, hardware=False, invalid=0):
        reset()
        items = [(config_tag, cfg(mode, padding=hardware, hash_final=final))]
        if state is not None:
            items.append((state_tag, list(state)))
        items.append((message_tag | (invalid << 8), list(data)))
        (pointer,) = pack(items, [32])
        return bytes.fromhex(read(pointer, 32))

    def software_hash(data, chunks):
        # SDK-style buffering: only full blocks are submitted on updates.
        # Carry real chaining state and append explicit padding at finalization.
        buffer, state, offset = b"", None, 0
        for size in chunks:
            buffer += data[offset:offset + size]
            offset += size
            count = len(buffer) // 64 * 64
            if count:
                state = operation(buffer[:count], state)
                buffer = buffer[count:]
        assert offset == len(data)
        padding = b"\x80" + b"\0" * ((55 - len(data)) % 64)
        padding += (len(data) * 8).to_bytes(8, "big")
        reset()
        items = [(config_tag, cfg(mode, padding=False, hash_final=True))]
        if state is not None:
            items.append((state_tag, list(state)))
        items.extend([(message_tag, list(buffer)), (message_tag, list(padding))])
        (pointer,) = pack(items, [32])
        return bytes.fromhex(read(pointer, 32))

    def message(length):
        return bytes((i * 13 + 7) & 255 for i in range(length))

    cases = [
        ("multi100", message(100), [64, 36]),
        ("single60", message(60), [60]),
        ("three100", message(100), [40, 40, 20]),
        ("c200", message(200), [100, 100]),
        ("midlead80", message(20) + b"\x80" + message(19), [20, 20]),
    ]
    results = []
    for name, data, chunks in cases:
        actual = software_hash(data, chunks).hex()
        expected = hashlib.sha256(data).hexdigest()
        results.append(dict(case=name, length=len(data), logical_update_sizes=chunks,
                            expected=expected, actual=actual, passed=actual == expected))
    actual = operation(b"\x80\0\0\0", final=True, hardware=True, invalid=3).hex()
    expected = hashlib.sha256(b"\x80").hexdigest()
    results.append(dict(case="one80", length=1, descriptor_length=4, InvalidBytesOrBits=3,
                        expected=expected, actual=actual, passed=actual == expected))
    for result in results:
        print(result["case"], "PASS" if result["passed"] else "FAIL")
    output = Path("output/hash-pr2-check")
    output.mkdir(parents=True, exist_ok=True)
    (output / "descriptor-results.json").write_text(json.dumps(results, indent=2) + "\n")
    assert all(result["passed"] for result in results)


if __name__ == "__main__":
    main()
