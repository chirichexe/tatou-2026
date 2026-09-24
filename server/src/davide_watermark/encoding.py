from __future__ import annotations

def bytes_to_bits(data: bytes) -> list[int]:
    """
    Convert bytes into an MSB-first bit list.

    e.g bytes_to_bits([1,1])
        [0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1]
    """
    bits: list[int] = []

    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)

    return bits


def bits_to_bytes(bits: list[int]) -> bytes:
    """
    Convert an MSB-first bit list into bytes.
    
    e.g. bits_to_bytes([1,1,0,0,0,1,1,0])
        b'\xc6'
    """
    if len(bits) % 8 != 0:
        raise ValueError("Bit sequence length must be a multiple of 8")

    output = bytearray()

    for i in range(0, len(bits), 8):
        byte = 0

        for bit in bits[i:i + 8]:
            byte = (byte << 1) | bit

        output.append(byte)

    return bytes(output)
