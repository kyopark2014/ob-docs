/**
 * Amazon Transcribe event stream encoding.
 * https://docs.aws.amazon.com/transcribe/latest/dg/streaming-setting-up.html#event-stream-encoding
 */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff]! ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function concatBytes(parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function writeUint32(value: number): Uint8Array {
  const buf = new Uint8Array(4);
  new DataView(buf.buffer).setUint32(0, value, false);
  return buf;
}

function encodeHeaders(headers: Record<string, string>): Uint8Array {
  const encoder = new TextEncoder();
  const chunks: Uint8Array[] = [];
  for (const [name, value] of Object.entries(headers)) {
    const nameBytes = encoder.encode(name);
    const valueBytes = encoder.encode(value);
    const buf = new Uint8Array(1 + nameBytes.length + 1 + 2 + valueBytes.length);
    let offset = 0;
    buf[offset] = nameBytes.length;
    offset += 1;
    buf.set(nameBytes, offset);
    offset += nameBytes.length;
    buf[offset] = 7;
    offset += 1;
    buf[offset] = (valueBytes.length >> 8) & 0xff;
    buf[offset + 1] = valueBytes.length & 0xff;
    offset += 2;
    buf.set(valueBytes, offset);
    chunks.push(buf);
  }
  return concatBytes(chunks);
}

export function encodeAudioEvent(pcmBytes: Uint8Array | ArrayBuffer): Uint8Array {
  const payload =
    pcmBytes instanceof Uint8Array ? pcmBytes : new Uint8Array(pcmBytes || 0);
  const headers = encodeHeaders({
    ":message-type": "event",
    ":event-type": "AudioEvent",
    ":content-type": "application/octet-stream",
  });
  const totalLength = 16 + headers.length + payload.length;
  const prelude = concatBytes([writeUint32(totalLength), writeUint32(headers.length)]);
  const preludeCrc = writeUint32(crc32(prelude));
  const body = concatBytes([prelude, preludeCrc, headers, payload]);
  const messageCrc = writeUint32(crc32(body));
  return concatBytes([body, messageCrc]);
}

function readHeaders(bytes: Uint8Array): Record<string, string> {
  const decoder = new TextDecoder();
  const headers: Record<string, string> = {};
  let offset = 0;
  while (offset < bytes.length) {
    const nameLen = bytes[offset]!;
    offset += 1;
    const name = decoder.decode(bytes.subarray(offset, offset + nameLen));
    offset += nameLen;
    const type = bytes[offset]!;
    offset += 1;
    if (type !== 7) break;
    const valueLen = (bytes[offset]! << 8) | bytes[offset + 1]!;
    offset += 2;
    headers[name] = decoder.decode(bytes.subarray(offset, offset + valueLen));
    offset += valueLen;
  }
  return headers;
}

export type EventStreamMessage = {
  headers: Record<string, string>;
  payload: Record<string, unknown> | null;
};

export function decodeEventStream(buffer: ArrayBuffer | Uint8Array): EventStreamMessage[] {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  const decoder = new TextDecoder();
  const messages: EventStreamMessage[] = [];
  let offset = 0;
  while (offset + 16 <= bytes.length) {
    const view = new DataView(bytes.buffer, bytes.byteOffset + offset);
    const totalLength = view.getUint32(0, false);
    const headersLength = view.getUint32(4, false);
    if (totalLength < 16 || offset + totalLength > bytes.length) break;
    const headersStart = offset + 12;
    const headersBytes = bytes.subarray(headersStart, headersStart + headersLength);
    const payloadStart = headersStart + headersLength;
    const payloadEnd = offset + totalLength - 4;
    const payloadBytes = bytes.subarray(payloadStart, payloadEnd);
    const headers = readHeaders(headersBytes);
    let payload: Record<string, unknown> | null = null;
    if (payloadBytes.length) {
      const text = decoder.decode(payloadBytes);
      try {
        payload = JSON.parse(text) as Record<string, unknown>;
      } catch {
        payload = { Message: text };
      }
    }
    messages.push({ headers, payload });
    offset += totalLength;
  }
  return messages;
}
