/* eslint-disable no-undef */
/**
 * Turns the microphone's float samples into what the server wants: Int16,
 * mono, 16 kHz, in frames of about 200 ms. Runs on the audio thread.
 *
 * Why here and not MediaRecorder: a webm chunk cannot be decoded without the
 * container header from the first one, so the server would have to hold an
 * ffmpeg pipe open per session. PCM at the pipeline's own sample rate is one
 * `np.frombuffer` on the other side.
 *
 * The AudioContext that owns this node is opened at 16 kHz, so the browser
 * resamples the microphone with a proper filter before the samples get here
 * and `sampleRate` is normally 16000. If a browser refuses that rate and
 * hands us 44.1 or 48 kHz, each output sample is the mean of the input
 * samples it covers -- a box filter, which is a crude low-pass but not
 * nothing. The first version dropped samples instead, and everything above
 * 8 kHz folded into the speech band.
 */
class Pcm16kProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000; // sampleRate is the AudioContext's, a global here
    this.acc = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(3200); // 200 ms at 16 kHz
    this.filled = 0;
  }

  emit(value) {
    const s = Math.max(-1, Math.min(1, value));
    this.out[this.filled++] = s < 0 ? s * 32768 : s * 32767;
    if (this.filled === this.out.length) {
      this.port.postMessage(this.out.buffer.slice(0));
      this.filled = 0;
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const mono = input[0];
    if (this.ratio === 1) {
      for (let i = 0; i < mono.length; i++) this.emit(mono[i]);
      return true;
    }
    for (let i = 0; i < mono.length; i++) {
      this.sum += mono[i];
      this.count += 1;
      this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        this.emit(this.sum / this.count);
        this.sum = 0;
        this.count = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-16k", Pcm16kProcessor);
