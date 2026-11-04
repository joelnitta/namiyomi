"""Wrap the existing, unmodified PNG icon sizes in a Windows ICO container."""
from pathlib import Path
import struct


def make_icon(assets):
    assets=Path(assets);pictures=[]
    for size in (16,32,128,256):
        data=(assets/'namiyomi.iconset'/f'icon_{size}x{size}.png').read_bytes()
        if data[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',data[16:24])!=(size,size):
            raise ValueError('Unexpected PNG icon dimensions.')
        pictures.append((size,data))
    offset=6+16*len(pictures);entries=[]
    for size,data in pictures:
        entries.append(struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(data),offset));offset+=len(data)
    (assets/'namiyomi.ico').write_bytes(struct.pack('<HHH',0,1,len(pictures))+b''.join(entries)+b''.join(data for _,data in pictures))


if __name__=='__main__':make_icon(Path(__file__).resolve().parents[1]/'assets')
