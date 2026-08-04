from base import *
from helper import *
import os
import shutil
import subprocess
import slogger

globalcounter = 0

_U64_MASK = (1 << 64) - 1
_XXH64_PRIME1 = 11400714785074694791
_XXH64_PRIME2 = 14029467366897019727
_XXH64_PRIME3 = 1609587929392839161
_XXH64_PRIME4 = 9650029242287828579
_XXH64_PRIME5 = 2870177450012600261


def _rotl64(value, bits):
    return ((value << bits) & _U64_MASK) | (value >> (64 - bits))


def _xxh64_round(acc, input64):
    acc = (acc + (input64 * _XXH64_PRIME2)) & _U64_MASK
    acc = _rotl64(acc, 31)
    acc = (acc * _XXH64_PRIME1) & _U64_MASK
    return acc


def _xxh64_merge_round(acc, val):
    acc ^= _xxh64_round(0, val)
    acc = (acc * _XXH64_PRIME1 + _XXH64_PRIME4) & _U64_MASK
    return acc


def xxh64(data, seed=0):
    """Return xxHash64(data, seed) compatible with Dolphin's XXH64(..., 0)."""
    if not isinstance(data, (bytes, bytearray)):
        data = bytes(data)
    length = len(data)
    i = 0

    if length >= 32:
        v1 = (seed + _XXH64_PRIME1 + _XXH64_PRIME2) & _U64_MASK
        v2 = (seed + _XXH64_PRIME2) & _U64_MASK
        v3 = seed & _U64_MASK
        v4 = (seed - _XXH64_PRIME1) & _U64_MASK

        limit = length - 32
        while i <= limit:
            v1 = _xxh64_round(v1, int.from_bytes(data[i:i + 8], 'little'))
            i += 8
            v2 = _xxh64_round(v2, int.from_bytes(data[i:i + 8], 'little'))
            i += 8
            v3 = _xxh64_round(v3, int.from_bytes(data[i:i + 8], 'little'))
            i += 8
            v4 = _xxh64_round(v4, int.from_bytes(data[i:i + 8], 'little'))
            i += 8

        h64 = (_rotl64(v1, 1) + _rotl64(v2, 7) + _rotl64(v3, 12) + _rotl64(v4, 18)) & _U64_MASK
        h64 = _xxh64_merge_round(h64, v1)
        h64 = _xxh64_merge_round(h64, v2)
        h64 = _xxh64_merge_round(h64, v3)
        h64 = _xxh64_merge_round(h64, v4)
    else:
        h64 = (seed + _XXH64_PRIME5) & _U64_MASK

    h64 = (h64 + length) & _U64_MASK

    while i + 8 <= length:
        k1 = _xxh64_round(0, int.from_bytes(data[i:i + 8], 'little'))
        h64 ^= k1
        h64 = (_rotl64(h64, 27) * _XXH64_PRIME1 + _XXH64_PRIME4) & _U64_MASK
        i += 8

    if i + 4 <= length:
        h64 ^= (int.from_bytes(data[i:i + 4], 'little') * _XXH64_PRIME1) & _U64_MASK
        h64 = (_rotl64(h64, 23) * _XXH64_PRIME2 + _XXH64_PRIME3) & _U64_MASK
        i += 4

    while i < length:
        h64 ^= (data[i] * _XXH64_PRIME5) & _U64_MASK
        h64 = (_rotl64(h64, 11) * _XXH64_PRIME1) & _U64_MASK
        i += 1

    h64 ^= h64 >> 33
    h64 = (h64 * _XXH64_PRIME2) & _U64_MASK
    h64 ^= h64 >> 29
    h64 = (h64 * _XXH64_PRIME3) & _U64_MASK
    h64 ^= h64 >> 32
    return h64 & _U64_MASK

class TEXPalette(FileChunk):
    def analyze(self):
        global globalcounter
        self.numTPLs = self.half()
        if self.numTPLs > 10000:
            raise Exception("Too many tpls: " + str(self.numTPLs))
        self.numCLUTsMaybe = self.half()
        # if self.numCLUTsMaybe != 0:
        #     print ("non-zero clut count at " + str(self.absolute))
        self.descriptors = []
        dataOffsets = []
        for i in range (0, self.numTPLs):
            descriptor = self.add_child(4 + i * 0x20, 0, TEXDescriptor, "tex header")
            self.descriptors.append(descriptor)
            descriptor.analyze()
            dataOffsets.append(descriptor.dataPtr)
            dataOffsets.append(descriptor.paletteDataPtr)
        if len(self.descriptors) == 0:
            return
        dataOffsets.sort()
        self.dataLens = {}
        for i in range(len(dataOffsets) - 1):
            ptr = dataOffsets[i]
            self.dataLens[ptr] = dataOffsets[i + 1] - ptr
            # self.dataLens[ptr] = dataOffsets[-1] - ptr
        self.dataLens[dataOffsets[-1]] = self.length - dataOffsets[-1]

class TEXDescriptor(FileChunk):
    def analyze(self):
        self.dataPtr = self.word()
        self.paletteDataPtr = self.word()
        # if self.paletteDataPtr != 0:
        #     print ("Palette at " + hex(self.absolute) + " in " + self.f.name)
        self.height = self.half()
        self.width = self.half()
        self.edgeLODEnable = self.byte()
        self.minLOD = self.byte()
        self.maxLOD = self.byte()
        self.unpacked = self.byte()
        self.word()
        self.read(3)
        self.format = self.byte()
        # if self.format != 0xe:
        # print ("format is " + hex(self.format))
        # print ("offset is " + hex(self.offset) + ", data ptr is " + hex(self.dataPtr) + '\n')
        self.paletteEntries = self.half()
        self.paletteFormat = self.byte()
        self.read(1)
        self.read(4)
        # Don't know where these are
        self.LODBias = 0
        self.wrapS = 0
        self.wrapT = 0
        self.minFilter = 0
        self.magFilter = 0
    
    def description(self):
        desc = super().description()
        desc += "\nData ptr: " + hex(self.parent.absolute + self.dataPtr)
        desc += "\nFormat: " + hex(self.format)
        return desc

    def _data_length(self):
        bits_per_pixel = {0:4,1:8,2:8,3:16,4:16,5:16,6:32,8:4,9:8,10:16,14:4}[self.format]
        return self.height * self.width * bits_per_pixel >> 3

    def _read_payload(self):
        data_length = self._data_length()
        self.parent.seek(self.dataPtr)
        image_data = self.parent.read(data_length)

        tlut_data = b''
        if self.paletteDataPtr > 0 and self.paletteEntries > 0:
            self.parent.seek(self.paletteDataPtr)
            tlut_data = self.parent.read(self.paletteEntries * 2)
        return image_data, tlut_data

    def _trim_tlut_to_used_range(self, image_data, tlut_data):
        tlut_size = len(tlut_data)
        if tlut_size == 16 * 2:
            min_idx = 0xffff
            max_idx = 0
            for b in image_data:
                low_nibble = b & 0x0F
                high_nibble = b >> 4
                min_idx = min(min_idx, low_nibble, high_nibble)
                max_idx = max(max_idx, low_nibble, high_nibble)
            tlut_size = 2 * (max_idx + 1 - min_idx)
            return tlut_data[2 * min_idx:2 * min_idx + tlut_size]
        if tlut_size == 256 * 2:
            min_idx = 0xffff
            max_idx = 0
            for b in image_data:
                min_idx = min(min_idx, b)
                max_idx = max(max_idx, b)
            tlut_size = 2 * (max_idx + 1 - min_idx)
            return tlut_data[2 * min_idx:2 * min_idx + tlut_size]
        if tlut_size == 16384 * 2:
            min_idx = 0xffff
            max_idx = 0
            for i in range(0, len(image_data) - 1, 2):
                texture_halfword = int.from_bytes(image_data[i:i + 2], 'big') & 0x3FFF
                min_idx = min(min_idx, texture_halfword)
                max_idx = max(max_idx, texture_halfword)
            tlut_size = 2 * (max_idx + 1 - min_idx)
            return tlut_data[2 * min_idx:2 * min_idx + tlut_size]
        return tlut_data

    def dolphinTextureBasenameForPayload(self, image_data, tlut_data=b''):
        """Return Dolphin-compatible base texture name for explicit payload bytes."""
        trimmed_tlut = self._trim_tlut_to_used_range(image_data, tlut_data) if tlut_data else b''

        tex_hash = xxh64(image_data, 0)
        tlut_hash = xxh64(trimmed_tlut, 0) if len(trimmed_tlut) > 0 else 0

        base_name = 'tex1_' + str(self.width) + 'x' + str(self.height)

        texture_name = format(tex_hash, '016x')
        tlut_name = '_' + format(tlut_hash, '016x') if len(trimmed_tlut) > 0 else ''
        format_name = str(self.format)
        return base_name + '_' + texture_name + tlut_name + '_' + format_name

    def ensureUniqueDolphinBasename(self, seen_names, max_attempts=8192):
        """Return basename and payload bytes, mutating image bytes only when needed."""
        image_data, tlut_data = self._read_payload()
        original_name = self.dolphinTextureBasenameForPayload(image_data, tlut_data)

        if original_name not in seen_names:
            seen_names.add(original_name)
            return {
                'basename': original_name,
                'image_data': image_data,
                'tlut_data': tlut_data,
                'changed': False,
                'attempts': 0,
                'warning': None,
                'original_basename': original_name,
            }

        if len(image_data) == 0:
            return {
                'basename': original_name,
                'image_data': image_data,
                'tlut_data': tlut_data,
                'changed': False,
                'attempts': 0,
                'warning': 'duplicate hash could not be untangled because image data is empty',
                'original_basename': original_name,
            }

        mutated = bytearray(image_data)
        img_len = len(mutated)
        for attempt in range(1, max_attempts + 1):
            pos = (attempt - 1) % img_len
            rounds = (attempt - 1) // img_len
            xor_mask = ((rounds * 37) + (attempt * 17) + 1) & 0xFF
            if xor_mask == 0:
                xor_mask = 1
            mutated[pos] ^= xor_mask

            candidate_name = self.dolphinTextureBasenameForPayload(mutated, tlut_data)
            if candidate_name not in seen_names:
                seen_names.add(candidate_name)
                return {
                    'basename': candidate_name,
                    'image_data': bytes(mutated),
                    'tlut_data': tlut_data,
                    'changed': True,
                    'attempts': attempt,
                    'warning': None,
                    'original_basename': original_name,
                }

        return {
            'basename': original_name,
            'image_data': image_data,
            'tlut_data': tlut_data,
            'changed': False,
            'attempts': max_attempts,
            'warning': f'duplicate hash untangle gave up after {max_attempts} attempts',
            'original_basename': original_name,
        }

    def toFile(self, path, image_data_override=None, tlut_data_override=None):
        dataLength = self._data_length()
        hasPalette = self.paletteDataPtr > 0
        fname = path + '.tpl'
        pngname = path + '.png'

        out = open(fname, 'wb')
        # TEXPalette
        out.write(itb(0x0020AF30, 4))
        out.write(itb(1, 4))
        out.write(itb(0xC, 4))

        # TEXDescriptor
        out.write(itb(0x14, 4))
        if hasPalette:
            out.write(itb(0x38, 4))
        else:
            out.write(itb(0, 4))

        # TEXHeader
        out.write(itb(self.height, 2))
        out.write(itb(self.width, 2))
        out.write(itb(self.format, 4))
        out.write(itb(0x40 + hasPalette * 0x20, 4))
        out.write(itb(self.wrapS, 4))
        out.write(itb(self.wrapT, 4))
        out.write(itb(self.minFilter, 4))
        out.write(itb(self.magFilter, 4))
        out.write(itb(self.LODBias, 4))
        out.write(itb(self.edgeLODEnable, 1))
        out.write(itb(self.minLOD, 1))
        out.write(itb(self.maxLOD, 1))
        out.write(itb(self.unpacked, 1))

        paletteDataLen = self.paletteEntries * 2
        paletteDataOffset = 0
        paletteDataPad = 0
        if hasPalette:
            paletteDataOffset = 0x60 + dataLength
            mod = paletteDataOffset % 0x20
            if mod != 0:
                paletteDataPad = 0x20 - mod
                paletteDataOffset += paletteDataPad
            # Palette header
            out.write(itb(self.paletteEntries, 2))
            out.write(itb(1, 1))
            out.write(itb(0, 1))
            out.write(itb(self.paletteFormat, 4))
            out.write(itb(paletteDataOffset, 4))

        # Pad to multiple of 0x20 (0x40 or 0x60 here)
        out.write(itb(0, 0x8 + hasPalette * 0x14))
        if image_data_override is None:
            self.parent.seek(self.dataPtr)
            out.write(self.parent.read(dataLength))
        else:
            if len(image_data_override) != dataLength:
                raise Exception('image_data_override length mismatch in TEX export')
            out.write(image_data_override)
        if hasPalette:
            out.write(itb(0, paletteDataPad))
            if tlut_data_override is None:
                self.parent.seek(self.paletteDataPtr)
                out.write(self.parent.read(paletteDataLen))
            else:
                if len(tlut_data_override) != paletteDataLen:
                    raise Exception('tlut_data_override length mismatch in TEX export')
                out.write(tlut_data_override)
        out.close()
        try:
            result = subprocess.run(
                ['wimgt', 'decode', '-q', '-d', pngname, fname],
                check=True,
                capture_output=True,
                text=True,
            )
            if not os.path.exists(pngname):
                detail = result.stderr.strip() or result.stdout.strip() or 'no diagnostic output'
                raise RuntimeError(f'wimgt completed without creating {pngname}: {detail}')
        except FileNotFoundError as exc:
            slogger.error(
                f'wimgt was not found on PATH while exporting texture {pngname}',
                source='texture.export',
            )
            raise RuntimeError('wimgt was not found on PATH') from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or f'exit code {exc.returncode}'
            slogger.error(
                f'wimgt failed to export texture {pngname}: {detail}',
                source='texture.export',
            )
            raise RuntimeError(f'wimgt failed to export texture {pngname}: {detail}') from exc
        finally:
            if os.path.exists(fname):
                os.remove(fname)

    def dolphinTextureBasename(self):
        """Return Dolphin-compatible base texture name (without .png)."""
        image_data, tlut_data = self._read_payload()
        return self.dolphinTextureBasenameForPayload(image_data, tlut_data)

    def toFileWithDolphinName(self, path, dolphin_path):
        """Export indexed name plus Dolphin-compatible hash name."""
        self.toFile(path)
        legacy_png = path + '.png'
        dolphin_png = dolphin_path + '.png'
        if legacy_png == dolphin_png:
            return
        if not os.path.exists(dolphin_png):
            shutil.copyfile(legacy_png, dolphin_png)