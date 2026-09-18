import { TARGET_SAMPLE_RATE } from "./config";

export function floatTo16BitPcm(float32: Float32Array, inputRate: number): Uint8Array {
  const ratio = inputRate / TARGET_SAMPLE_RATE;
  const newLen =
    inputRate === TARGET_SAMPLE_RATE
      ? float32.length
      : Math.round(float32.length / ratio);
  const buffer = new ArrayBuffer(newLen * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < newLen; i += 1) {
    let sample: number;
    if (inputRate === TARGET_SAMPLE_RATE) {
      sample = float32[i]!;
    } else {
      const x = i * ratio;
      const i0 = Math.floor(x);
      const i1 = Math.min(i0 + 1, float32.length - 1);
      sample = float32[i0]! + (float32[i1]! - float32[i0]!) * (x - i0);
    }
    const clipped = Math.max(-1, Math.min(1, sample));
    view.setInt16(i * 2, clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

export function encodeWav(
  chunks: Uint8Array[],
  sampleRate = TARGET_SAMPLE_RATE,
): Blob {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  const buffer = new ArrayBuffer(44 + total);
  const view = new DataView(buffer);
  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i += 1) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + total, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, total, true);
  const out = new Uint8Array(buffer);
  let offset = 44;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export function recordedSeconds(chunks: Uint8Array[]): number {
  const bytes = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  return bytes / 2 / TARGET_SAMPLE_RATE;
}
