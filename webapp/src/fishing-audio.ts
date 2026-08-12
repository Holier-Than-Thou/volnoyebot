type AudioStatus = "locked" | "running" | "muted" | "unsupported";

type AudioStatusListener = (status: AudioStatus) => void;
type EffectName = "cast" | "bobberLand" | "bite" | "fightSplash1" | "fightSplash2" | "fightSplash3";

const AUDIO_PREFERENCE_KEY = "fishing-audio-enabled";
const STEP_DURATION = 0.36;
const MUSIC_VOLUME = 0.13;
const AMBIENCE_VOLUME = 0.15;
const EFFECT_PATHS: Record<EffectName, string> = {
  cast: "assets/fishing/audio/cast.wav",
  bobberLand: "assets/fishing/audio/bobber-land.wav",
  bite: "assets/fishing/audio/bite.wav",
  fightSplash1: "assets/fishing/audio/fight-splash-1.wav",
  fightSplash2: "assets/fishing/audio/fight-splash-2.wav",
  fightSplash3: "assets/fishing/audio/fight-splash-3.wav",
};

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
  private effectBuffers = new Map<EffectName, AudioBuffer>();
  private effectLoads = new Map<EffectName, Promise<AudioBuffer>>();
  private fightSplashIndex = 0;
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
      void this.preloadEffects();
    }
    this.reportStatus();
  }

  async playCast(): Promise<void> {
    await this.unlock();
    if (!this.canPlay()) return;
    this.duckBackground(0.58);
    await this.playEffect("cast", 0.95);
  }

  async playLandingSplash(): Promise<void> {
    await this.unlock();
    if (!this.canPlay()) return;
    this.duckBackground(0.72);
    await this.playEffect("bobberLand", 0.95);
  }

  async playBite(): Promise<void> {
    await this.unlock();
    if (!this.canPlay()) return;
    this.duckBackground(0.62);
    await this.playEffect("bite", 0.9);
  }

  playBobberSplash(intensity = 0.7): void {
    if (!this.canPlay()) return;
    this.duckBackground(0.34, 0.62);
    const splashNames = ["fightSplash1", "fightSplash2", "fightSplash3"] as const;
    const splashName = splashNames[this.fightSplashIndex % splashNames.length];
    this.fightSplashIndex += 1;
    const volume = Math.max(0.55, Math.min(1.05, intensity)) * 0.86;
    const playbackRate = 0.94 + this.random() * 0.12;
    void this.playEffect(splashName, volume, playbackRate);
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
    this.effectsGain.gain.value = 0.92;
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

  private async preloadEffects(): Promise<void> {
    await Promise.allSettled(
      (Object.keys(EFFECT_PATHS) as EffectName[]).map((name) => this.loadEffect(name)),
    );
  }

  private loadEffect(name: EffectName): Promise<AudioBuffer> {
    const existingBuffer = this.effectBuffers.get(name);
    if (existingBuffer) return Promise.resolve(existingBuffer);
    const existingLoad = this.effectLoads.get(name);
    if (existingLoad) return existingLoad;
    if (!this.context) return Promise.reject(new Error("Audio context is not initialized"));

    const url = `${import.meta.env.BASE_URL}${EFFECT_PATHS[name]}`;
    const loading = fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`Unable to load audio effect: ${url}`);
        return response.arrayBuffer();
      })
      .then((data) => this.context?.decodeAudioData(data))
      .then((buffer) => {
        if (!buffer) throw new Error("Audio context was closed while loading an effect");
        this.effectBuffers.set(name, buffer);
        return buffer;
      })
      .finally(() => this.effectLoads.delete(name));
    this.effectLoads.set(name, loading);
    return loading;
  }

  private async playEffect(
    name: EffectName,
    volume: number,
    playbackRate = 1,
  ): Promise<void> {
    try {
      const buffer = await this.loadEffect(name);
      if (!this.canPlay() || !this.context || !this.effectsGain) return;
      const source = this.context.createBufferSource();
      const gain = this.context.createGain();
      source.buffer = buffer;
      source.playbackRate.value = playbackRate;
      gain.gain.value = volume;
      source.connect(gain).connect(this.effectsGain);
      source.start();
    } catch {
      // A missing effect should not interrupt the fishing game.
    }
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
