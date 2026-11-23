// Mic capture: resample from the device rate to 16 kHz and emit 512-sample PCM16 frames (1024 bytes),
// exactly what Prisma v3.0 realtime expects. Runs on the audio thread.
class Capture16k extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;          // fractional read position into the incoming stream
    this.acc = 0; this.accN = 0;
    this.out = new Int16Array(512);
    this.outN = 0;
    this.level = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      // box-filter average over each output sample's span (cheap anti-aliasing)
      this.acc += ch[i]; this.accN++;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        let s = this.acc / this.accN; this.acc = 0; this.accN = 0;
        s = Math.max(-1, Math.min(1, s));
        this.level = Math.max(this.level * 0.995, Math.abs(s));
        this.out[this.outN++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        if (this.outN === 512) {
          const buf = this.out.buffer.slice(0);
          this.port.postMessage({ pcm: buf, level: this.level }, [buf]);
          this.outN = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('capture-16k', Capture16k);
