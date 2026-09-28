// Tablet microphone capture for Barnaby: forwards raw mono Float32 frames to the page.
// Loaded as a same-origin module so the tablet page's CSP (script-src 'self') allows it.
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
