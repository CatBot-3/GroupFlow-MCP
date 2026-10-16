/** Optional voice: browser speech recognition for input and speech synthesis for replies. */

type Recognition = {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
};

const Ctor = (typeof window !== 'undefined'
  ? (window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition }).SpeechRecognition
    ?? (window as unknown as { webkitSpeechRecognition?: new () => Recognition }).webkitSpeechRecognition
  : undefined);

export const canListen = !!Ctor;
export const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window;

export function listen(onText: (text: string) => void, onEnd: (error?: string) => void): () => void {
  if (!Ctor) {
    onEnd('Voice input is not supported in this browser. Try Chrome or Edge.');
    return () => undefined;
  }
  const r = new Ctor();
  r.lang = 'en-US';
  r.interimResults = false;
  r.maxAlternatives = 1;
  let error: string | undefined;
  r.onresult = (e) => {
    const text = e.results[0]?.[0]?.transcript;
    if (text) onText(text);
  };
  r.onerror = (e) => {
    error = e.error === 'not-allowed' ? 'Microphone access was blocked. Allow it in the browser to talk to Alexa.' : `Voice input stopped: ${e.error}.`;
  };
  r.onend = () => onEnd(error);
  r.start();
  return () => r.stop();
}

export function speak(text: string): void {
  if (!canSpeak) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.04;
  window.speechSynthesis.speak(u);
}
