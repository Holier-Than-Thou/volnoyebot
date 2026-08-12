type AudioStatus = "locked" | "running" | "muted" | "unsupported";

type AudioStatusListener = (status: AudioStatus) => void;

const AUDIO_PREFERENCE_KEY = "fishing-audio-enabled";
const STEP_DURATION = 0.36;
const MUSIC_VOLUME = 0.13;
const AMBIENCE_VOLUME = 0.15;

const midiFrequency = (note: number): number => 440 * 2 ** ((note - 69) / 12);

export class FishingAudio {
  private context?: AudioContext;
  private masterGain?: GainNode;
  private musicGain?: GainNode;
  private ambienceGain?: GainNode;
  private effectsGain?: GainNode;
  private enabled = this.readPreference();
  private musicTimer?: number;
  private birdTimer?: number;
  private suspendTimer?: number;
  private musicStep = 0;
  private nextMusicStepAt = 0;
  private backgroundSources = new Set<AudioScheduledSourceNode>();
  private randomState = this.createRandomSeed();

  constructor(private readonly onStatusChange: AudioStatusListener) {
    this.reportStatus();
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  async toggle(): Promise<void> {
    await this.setEnabled(!this.enabled);
  }

  async setEnabled(enabled: boolean): Promise<void> {
    this.enabled = enabled;
    this.savePreference();

    if (!enabled) {
      this.stopBeds();
      this.fadeMaster(0.0001, 0.06);
      this.reportStatus();
      if (this.context) {
        window.clearTimeout(this.suspendTimer);
        this.suspendTimer = window.setTimeout(() => {
          void this.context?.suspend();
        }, 90);
      }
      return;
    }

    await this.unlock();
  }

  async unlock(): Promise<void> {
    if (!this.enabled) {
      this.reportStatus();
      return;
    }

    if (!this.ensureContext()) {
      this.reportStatus();
      return;
    }

    window.clearTimeout(this.suspendTimer);
    try {
      if (this.context?.state === "suspended") await this.context.resume();
    } catch {
      this.reportStatus();
      return;
    }

    if (this.context?.state === "running") {
      this.fadeMaster(0.72, 0.08);
      this.startBeds();
    }
    this.reportStatus();
  }

  async playCast(): Promise<void> {
    await this.unlock();
    if (!this.canPlay()) return;
    this.duckBackground(0.58);
    this.playNoiseSweep(0.48, 2100, 380, 0.42);
    this.playTone(330, 0.025, 0.3, 0.17, "triangle", this.effectsGain);
  }

  async playLandingSplash(): Promise<void> {
    await this.unlock();
    if (!this.canPlay()) return;
    this.duckBackground(0.48);
    this.playSplash(1.12);
  }

  async playBite(): Promise<void> {
    await this.unlock();
    if (!this.canPlay() || !this.context) return;
    this.duckBackground(0.62);
    const now = this.context.currentTime;
    this.playTone(659.25, 0.01, 0.16, 0.22, "square", this.effectsGain, now);
    this.playTone(987.77, 0.01, 0.2, 0.2, "square", this.effectsGain, now + 0.13);
    this.playTone(1318.51, 0.008, 0.12, 0.12, "square", this.effectsGain, now + 0.27);
  }

  playBobberSplash(intensity = 0.7): void {
    if (!this.canPlay()) return;
    this.duckBackground(0.34, 0.62);
    this.playSplash(Math.max(0.55, Math.min(1.15, intensity)));
  }

  dispose(): void {
    this.stopBeds();
    window.clearTimeout(this.suspendTimer);
    if (this.context && this.context.state !== "closed") void this.context.close();
  }

  private readPreference(): boolean {
    try {
      return window.localStorage.getItem(AUDIO_PREFERENCE_KEY) !== "off";
    } catch {
      return true;
    }
  }

  private savePreference(): void {
    try {
      window.localStorage.setItem(AUDIO_PREFERENCE_KEY, this.enabled ? "on" : "off");
    } catch {
      // The game remains usable when storage is unavailable.
    }
  }

  private ensureContext(): boolean {
    if (this.context) return true;
    const AudioContextConstructor = window.AudioContext
      ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextConstructor) return false;

    this.context = new AudioContextConstructor();
    this.masterGain = this.context.createGain();
    this.musicGain = this.context.createGain();
    this.ambienceGain = this.context.createGain();
    this.effectsGain = this.context.createGain();

    this.masterGain.gain.value = 0.0001;
    this.musicGain.gain.value = MUSIC_VOLUME;
    this.ambienceGain.gain.value = AMBIENCE_VOLUME;
    this.effectsGain.gain.value = 0.76;
    this.musicGain.connect(this.masterGain);
    this.ambienceGain.connect(this.masterGain);
    this.effectsGain.connect(this.masterGain);
    this.masterGain.connect(this.context.destination);
    this.context.addEventListener("statechange", () => this.reportStatus());
    return true;
  }

  private canPlay(): boolean {
    return Boolean(this.enabled && this.context?.state === "running");
  }

  private currentStatus(): AudioStatus {
    if (!this.enabled) return "muted";
    if (!window.AudioContext
      && !(window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext) {
      return "unsupported";
    }
    return this.context?.state === "running" ? "running" : "locked";
  }

  private reportStatus(): void {
    this.onStatusChange(this.currentStatus());
  }

  private fadeMaster(value: number, duration: number): void {
    if (!this.context || !this.masterGain) return;
    const now = this.context.currentTime;
    this.masterGain.gain.cancelScheduledValues(now);
    this.masterGain.gain.setValueAtTime(Math.max(0.0001, this.masterGain.gain.value), now);
    this.masterGain.gain.exponentialRampToValueAtTime(Math.max(0.0001, value), now + duration);
  }

  private duckBackground(duration: number, depth = 0.42): void {
    if (!this.context || !this.musicGain || !this.ambienceGain) return;
    const now = this.context.currentTime;
    const restoreAt = now + duration;
    const duck = (gain: GainNode, normalVolume: number): void => {
      gain.gain.cancelScheduledValues(now);
      gain.gain.setValueAtTime(Math.max(0.0001, gain.gain.value), now);
      gain.gain.exponentialRampToValueAtTime(normalVolume * depth, now + 0.025);
      gain.gain.exponentialRampToValueAtTime(normalVolume, restoreAt);
    };
    duck(this.musicGain, MUSIC_VOLUME);
    duck(this.ambienceGain, AMBIENCE_VOLUME);
  }

  private startBeds(): void {
    if (!this.context || !this.ambienceGain || this.musicTimer !== undefined) return;
    this.startAmbience();
    this.musicStep = 0;
    this.nextMusicStepAt = this.context.currentTime + 0.08;
    this.scheduleMusic();
    this.scheduleBird();
  }

  private stopBeds(): void {
    window.clearTimeout(this.musicTimer);
    window.clearTimeout(this.birdTimer);
    this.musicTimer = undefined;
    this.birdTimer = undefined;
    this.backgroundSources.forEach((source) => {
      try {
        source.stop();
      } catch {
        // A source may already have finished naturally.
      }
    });
    this.backgroundSources.clear();
  }

  private startAmbience(): void {
    if (!this.context || !this.ambienceGain) return;
    const noiseBuffer = this.createNoiseBuffer(3.5);

    const water = this.context.createBufferSource();
    const waterFilter = this.context.createBiquadFilter();
    const waterGain = this.context.createGain();
    water.buffer = noiseBuffer;
    water.loop = true;
    waterFilter.type = "bandpass";
    waterFilter.frequency.value = 390;
    waterFilter.Q.value = 0.55;
    waterGain.gain.value = 0.32;
    water.connect(waterFilter).connect(waterGain).connect(this.ambienceGain);
    water.start();
    this.trackBackgroundSource(water);

    const wind = this.context.createBufferSource();
    const windFilter = this.context.createBiquadFilter();
    const windGain = this.context.createGain();
    const windLfo = this.context.createOscillator();
    const windLfoGain = this.context.createGain();
    wind.buffer = noiseBuffer;
    wind.loop = true;
    windFilter.type = "lowpass";
    windFilter.frequency.value = 720;
    windGain.gain.value = 0.12;
    windLfo.frequency.value = 0.08;
    windLfoGain.gain.value = 0.045;
    windLfo.connect(windLfoGain).connect(windGain.gain);
    wind.connect(windFilter).connect(windGain).connect(this.ambienceGain);
    wind.start();
    windLfo.start();
    this.trackBackgroundSource(wind);
    this.trackBackgroundSource(windLfo);
  }

  private createNoiseBuffer(duration: number): AudioBuffer {
    if (!this.context) throw new Error("Audio context is not initialized");
    const frameCount = Math.floor(this.context.sampleRate * duration);
    const buffer = this.context.createBuffer(1, frameCount, this.context.sampleRate);
    const data = buffer.getChannelData(0);
    let previous = 0;
    for (let index = 0; index < frameCount; index += 1) {
      const random = this.random() * 2 - 1;
      previous = previous * 0.82 + random * 0.18;
      data[index] = previous;
    }
    return buffer;
  }

  private scheduleMusic(): void {
    if (!this.context || !this.musicGain || !this.enabled) return;
    const melody = [72, null, 76, 79, null, 76, 74, null, 69, null, 72, 76, 74, 72, 69, null] as const;
    const bass = [48, 48, 45, 43] as const;
    const horizon = this.context.currentTime + 0.85;

    while (this.nextMusicStepAt < horizon) {
      const note = melody[this.musicStep % melody.length];
      if (note !== null) {
        this.playTone(
          midiFrequency(note),
          0.025,
          STEP_DURATION * 0.58,
          0.1,
          "square",
          this.musicGain,
          this.nextMusicStepAt,
        );
      }
      if (this.musicStep % 4 === 0) {
        const bassNote = bass[Math.floor(this.musicStep / 4) % bass.length];
        this.playTone(
          midiFrequency(bassNote),
          0.04,
          STEP_DURATION * 2.7,
          0.13,
          "triangle",
          this.musicGain,
          this.nextMusicStepAt,
        );
      }
      this.musicStep = (this.musicStep + 1) % melody.length;
      this.nextMusicStepAt += STEP_DURATION;
    }

    this.musicTimer = window.setTimeout(() => this.scheduleMusic(), 260);
  }

  private scheduleBird(): void {
    window.clearTimeout(this.birdTimer);
    this.birdTimer = window.setTimeout(() => {
      if (this.canPlay() && this.context) {
        const now = this.context.currentTime;
        const baseFrequency = 1250 + this.random() * 420;
        this.playChirp(baseFrequency, now);
        this.playChirp(baseFrequency * 1.14, now + 0.18);
      }
      if (this.enabled) this.scheduleBird();
    }, 6500 + this.random() * 7000);
  }

  private playChirp(frequency: number, startAt: number): void {
    if (!this.context || !this.ambienceGain) return;
    const oscillator = this.context.createOscillator();
    const gain = this.context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(frequency, startAt);
    oscillator.frequency.exponentialRampToValueAtTime(frequency * 1.32, startAt + 0.08);
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(0.07, startAt + 0.018);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + 0.12);
    oscillator.connect(gain).connect(this.ambienceGain);
    oscillator.start(startAt);
    oscillator.stop(startAt + 0.13);
  }

  private playTone(
    frequency: number,
    attack: number,
    duration: number,
    volume: number,
    type: OscillatorType,
    destination: AudioNode | undefined,
    startAt = this.context?.currentTime ?? 0,
  ): void {
    if (!this.context || !destination) return;
    const oscillator = this.context.createOscillator();
    const gain = this.context.createGain();
    const endAt = startAt + duration;
    oscillator.type = type;
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(volume, startAt + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, endAt);
    oscillator.connect(gain).connect(destination);
    oscillator.start(startAt);
    oscillator.stop(endAt + 0.02);
    if (destination !== this.effectsGain) this.trackBackgroundSource(oscillator);
  }

  private playNoiseSweep(
    duration: number,
    startFrequency: number,
    endFrequency: number,
    volume: number,
  ): void {
    if (!this.context || !this.effectsGain) return;
    const now = this.context.currentTime;
    const source = this.context.createBufferSource();
    const filter = this.context.createBiquadFilter();
    const gain = this.context.createGain();
    source.buffer = this.createNoiseBuffer(duration + 0.05);
    filter.type = "bandpass";
    filter.Q.value = 0.75;
    filter.frequency.setValueAtTime(startFrequency, now);
    filter.frequency.exponentialRampToValueAtTime(endFrequency, now + duration);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(volume, now + 0.025);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
    source.connect(filter).connect(gain).connect(this.effectsGain);
    source.start(now);
    source.stop(now + duration + 0.02);
  }

  private playSplash(intensity: number): void {
    if (!this.context) return;
    this.playNoiseSweep(0.34, 1650, 310, 0.31 * intensity);
    const now = this.context.currentTime;
    this.playTone(330, 0.008, 0.16, 0.13 * intensity, "sine", this.effectsGain, now);
    this.playTone(220, 0.008, 0.25, 0.12 * intensity, "sine", this.effectsGain, now + 0.045);
  }

  private trackBackgroundSource(source: AudioScheduledSourceNode): void {
    this.backgroundSources.add(source);
    source.addEventListener("ended", () => this.backgroundSources.delete(source), { once: true });
  }

  private createRandomSeed(): number {
    try {
      const seed = new Uint32Array(1);
      window.crypto.getRandomValues(seed);
      return seed[0] || 0x61c88647;
    } catch {
      return 0x61c88647;
    }
  }

  private random(): number {
    this.randomState = (
      Math.imul(this.randomState, 1664525) + 1013904223
    ) >>> 0;
    return this.randomState / 4294967296;
  }
}
