import os
import re
import math
import numpy as np
import soundfile as sf
import librosa
import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage
from pathlib import Path
from basic_pitch.inference import predict
from basic_pitch import ICASSP_2022_MODEL_PATH

# Standard General MIDI Drum Notes (Channel 10)
GM_DRUM_MAP = {
    "kick": 36,       # Bass Drum 1 (C1)
    "bombo": 36,
    "snare": 38,      # Acoustic Snare (D1)
    "caja": 38,
    "side_stick": 37,
    "clap": 39,
    "hihat_closed": 42, # Closed Hi-Hat (F#1)
    "hihat_open": 46,   # Open Hi-Hat (A#1)
    "hihat_pedal": 44,
    "hihat": 42,
    "tom_low": 45,    # Low Tom
    "tom_mid": 47,    # Mid Tom
    "tom_high": 50,   # High Tom
    "toms": 47,
    "crash": 49,      # Crash Cymbal 1 (C#2)
    "ride": 51,       # Ride Cymbal 1 (D#2)
    "tambourine": 54, # Tambourine
    "cowbell": 56,    # Cowbell
    "conga_high": 62, # Mute Hi Conga
    "conga_low": 64,  # Low Conga
    "congas": 64,
}

# GM Instrument Programs for standard melodic tracks
GM_PROGRAMS = {
    "drums": 0,       # Drum kit (uses Channel 9/10)
    "bateria": 0,
    "bass": 33,       # Electric Bass (finger)
    "bajo": 33,
    "piano": 0,       # Acoustic Grand Piano
    "teclados": 0,
    "keys": 4,        # Electric Piano 1 (Rhodes)
    "guitar": 25,     # Acoustic Guitar (steel)
    "guitarra": 25,
    "electric_guitar": 27, # Electric Guitar (clean)
    "vocals": 54,     # Voice Oohs / Synth Voice
    "vocales": 54,
    "melody": 80,     # Lead 1 (square)
    "lead": 80,
    "synth": 81,      # Lead 2 (sawtooth)
    "sintetizador": 81,
    "strings": 48,    # String Ensemble 1
    "cuerdas": 48,
    "wind": 73,       # Flute
    "vientos": 73,
    "other": 88,      # Pad 1 (new age)
    "otro": 88
}


def detect_bpm_and_beats(audio_path, sr=22050):
    """
    Analiza el archivo de audio y detecta el BPM exacto y la rejilla de pulsos (beats).
    """
    try:
        y, sample_rate = librosa.load(str(audio_path), sr=sr, mono=True)
        if len(y) == 0:
            return 120.0, []
        
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sample_rate)
        # Handle numpy array or scalar
        if isinstance(tempo, (np.ndarray, list)):
            tempo_val = float(tempo[0]) if len(tempo) > 0 else 120.0
        else:
            tempo_val = float(tempo)
        
        if tempo_val <= 0 or math.isnan(tempo_val):
            tempo_val = 120.0
            
        beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate).tolist()
        return round(tempo_val, 2), beat_times
    except Exception as e:
        print(f"[midi_engine] Error detectando BPM: {e}")
        return 120.0, []


def transcribe_drums_to_midi(audio_path, bpm=120.0, stem_label="Drums"):
    """
    Transcribe pistas de percusion o bateria analizando transientes y bandas de frecuencia
    para asignarlas a notas del mapa General MIDI (Canal 10).
    """
    try:
        y, sr = librosa.load(str(audio_path), sr=44100, mono=True)
    except Exception as e:
        print(f"[midi_engine] Error cargando audio para bateria: {e}")
        return []

    if len(y) == 0:
        return []

    notes = []
    
    # If the stem is a dedicated single drum piece (e.g. Kick, Snare, HiHat from DrumSep)
    label_clean = stem_label.lower().replace(" ", "").replace("_", "")
    single_piece_pitch = None
    for k, v in GM_DRUM_MAP.items():
        if k in label_clean:
            single_piece_pitch = v
            break

    if single_piece_pitch is not None and "bateria" not in label_clean and "drums" not in label_clean:
        # Single isolated percussion track
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, backtrack=False, units="time")
        
        for t in onsets:
            sample_idx = min(int(t * sr), len(y) - 1)
            window = y[max(0, sample_idx - 100):min(len(y), sample_idx + 1000)]
            peak = np.max(np.abs(window)) if len(window) > 0 else 0.5
            vel = int(np.clip(peak * 160 + 40, 45, 127))
            notes.append({
                "pitch": single_piece_pitch,
                "start_time": float(t),
                "duration": 0.1,
                "velocity": vel,
                "channel": 9 # 0-indexed channel 9 = MIDI channel 10
            })
        return notes

    # Multi-component Drum Stem: Separate by frequency sub-bands for Kick, Snare, and Hi-Hat
    try:
        stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        
        kick_bins = freqs < 135
        snare_bins = (freqs >= 180) & (freqs < 2800)
        hihat_bins = freqs >= 4500

        # Kick envelope
        kick_spec = np.mean(stft[kick_bins, :], axis=0) if np.any(kick_bins) else None
        if kick_spec is not None and np.max(kick_spec) > 0:
            kick_spec = kick_spec / np.max(kick_spec)
            kick_onsets = librosa.onset.onset_detect(onset_envelope=kick_spec, sr=sr, hop_length=512, delta=0.18, units="time")
            for t in kick_onsets:
                notes.append({
                    "pitch": 36, # Bass Drum (C1)
                    "start_time": float(t),
                    "duration": 0.1,
                    "velocity": 105,
                    "channel": 9
                })

        # Snare envelope
        snare_spec = np.mean(stft[snare_bins, :], axis=0) if np.any(snare_bins) else None
        if snare_spec is not None and np.max(snare_spec) > 0:
            snare_spec = snare_spec / np.max(snare_spec)
            snare_onsets = librosa.onset.onset_detect(onset_envelope=snare_spec, sr=sr, hop_length=512, delta=0.22, units="time")
            for t in snare_onsets:
                notes.append({
                    "pitch": 38, # Acoustic Snare (D1)
                    "start_time": float(t),
                    "duration": 0.1,
                    "velocity": 100,
                    "channel": 9
                })

        # Hi-Hat envelope
        hihat_spec = np.mean(stft[hihat_bins, :], axis=0) if np.any(hihat_bins) else None
        if hihat_spec is not None and np.max(hihat_spec) > 0:
            hihat_spec = hihat_spec / np.max(hihat_spec)
            hihat_onsets = librosa.onset.onset_detect(onset_envelope=hihat_spec, sr=sr, hop_length=512, delta=0.15, units="time")
            for t in hihat_onsets:
                notes.append({
                    "pitch": 42, # Closed Hi-Hat (F#1)
                    "start_time": float(t),
                    "duration": 0.08,
                    "velocity": 85,
                    "channel": 9
                })

    except Exception as e:
        print(f"[midi_engine] Error en separacion espectral de bateria: {e}")
        onset_times = librosa.onset.onset_detect(y=y, sr=sr, units="time")
        for t in onset_times:
            notes.append({
                "pitch": 38,
                "start_time": float(t),
                "duration": 0.1,
                "velocity": 90,
                "channel": 9
            })

    notes.sort(key=lambda x: x["start_time"])
    return notes


def transcribe_tonal_to_midi(audio_path, instrument_type="piano", onset_thresh=0.5, frame_thresh=0.3, min_note_len=58):
    """
    Transcribe pistas melodicas/polifonicas (Piano, Bajo, Guitarra, Vocales, Sintes)
    a eventos de notas MIDI con Basic Pitch (ONNX).
    """
    inst_lower = instrument_type.lower()
    if "bass" in inst_lower or "bajo" in inst_lower:
        onset_thresh = 0.4
        frame_thresh = 0.25
        min_note_len = 80
    elif "vocal" in inst_lower or "voz" in inst_lower or "canto" in inst_lower:
        onset_thresh = 0.55
        frame_thresh = 0.35
        min_note_len = 70
    elif "guitar" in inst_lower or "guitarra" in inst_lower:
        onset_thresh = 0.45
        frame_thresh = 0.28
        min_note_len = 50
    elif "piano" in inst_lower or "teclado" in inst_lower or "keys" in inst_lower:
        onset_thresh = 0.48
        frame_thresh = 0.30
        min_note_len = 45

    try:
        model_output, midi_data, note_events = predict(
            str(audio_path),
            onset_threshold=onset_thresh,
            frame_threshold=frame_thresh,
            minimum_note_length=min_note_len,
            melodia_trick=True if ("vocal" in inst_lower or "bass" in inst_lower) else False
        )
        
        notes = []
        for note in note_events:
            start_s = float(note[0])
            end_s = float(note[1])
            pitch = int(round(note[2]))
            amp = float(note[3])
            
            if "bass" in inst_lower or "bajo" in inst_lower:
                if pitch > 60:
                    pitch -= 12 * int(math.ceil((pitch - 60) / 12))
            pitch = max(21, min(108, pitch))
            
            dur = max(0.05, end_s - start_s)
            vel = int(np.clip(amp * 127, 30, 127))
            
            notes.append({
                "pitch": pitch,
                "start_time": start_s,
                "duration": dur,
                "velocity": vel,
                "channel": 0
            })
            
        notes.sort(key=lambda x: x["start_time"])
        return notes
    except Exception as e:
        print(f"[midi_engine] Error en transcripcion tonal con Basic Pitch ({instrument_type}): {e}")
        return []


def create_multitrack_midi(stems_dict, output_midi_path, bpm=120.0, song_title="Song"):
    """
    Crea un archivo MIDI Formato 1 con todas las pistas organizadas,
    nombres legibles, canales correspondientes y tempo incrustado.
    """
    ticks_per_beat = 480
    mid = MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    tempo_val = mido.bpm2tempo(max(20.0, min(300.0, bpm)))

    # Track 0: Master Conductor Track (Tempo & Meta)
    conductor_track = MidiTrack()
    mid.tracks.append(conductor_track)
    conductor_track.append(MetaMessage('track_name', name=f"{song_title} (BPM: {bpm})", time=0))
    conductor_track.append(MetaMessage('set_tempo', tempo=tempo_val, time=0))
    conductor_track.append(MetaMessage('time_signature', numerator=4, denominator=4, clocks_per_click=24, notated_32nd_notes_per_beat=8, time=0))
    conductor_track.append(MetaMessage('end_of_track', time=0))

    tonal_channel_counter = 0
    track_order = ["drums", "bateria", "kick", "snare", "hihat", "bass", "bajo", "piano", "teclados", "keys", "guitar", "guitarra", "vocals", "vocales", "melody", "lead", "other", "otro"]
    
    def get_sort_key(label):
        lbl = label.lower()
        for idx, pattern in enumerate(track_order):
            if pattern in lbl:
                return idx
        return 99

    sorted_stems = sorted(stems_dict.keys(), key=get_sort_key)
    total_notes_count = 0
    exported_tracks_info = []

    for stem_name in sorted_stems:
        note_list = stems_dict[stem_name]
        if not note_list:
            continue

        is_drum = any(k in stem_name.lower() for k in ["drum", "bateria", "kick", "snare", "hihat", "perc"])
        
        if is_drum:
            channel = 9 # MIDI Channel 10
            program = 0
        else:
            if tonal_channel_counter == 9:
                tonal_channel_counter += 1
            channel = tonal_channel_counter % 16
            tonal_channel_counter += 1
            
            program = 0
            for k, prog_num in GM_PROGRAMS.items():
                if k in stem_name.lower():
                    program = prog_num
                    break

        track = MidiTrack()
        mid.tracks.append(track)
        
        track.append(MetaMessage('track_name', name=stem_name, time=0))
        track.append(Message('program_change', program=program, channel=channel, time=0))

        ticks_per_second = (bpm / 60.0) * ticks_per_beat

        events = []
        for n in note_list:
            start_ticks = int(round(n["start_time"] * ticks_per_second))
            dur_ticks = max(10, int(round(n["duration"] * ticks_per_second)))
            end_ticks = start_ticks + dur_ticks
            pitch = int(max(0, min(127, n["pitch"])))
            vel = int(max(1, min(127, n.get("velocity", 90))))
            note_chan = n.get("channel", channel)

            events.append((start_ticks, 'note_on', pitch, vel, note_chan))
            events.append((end_ticks, 'note_off', pitch, 0, note_chan))

        events.sort(key=lambda x: (x[0], 0 if x[1] == 'note_off' else 1))

        last_tick = 0
        for tick, ev_type, pitch, vel, ev_chan in events:
            delta = max(0, tick - last_tick)
            last_tick = tick
            track.append(Message(ev_type, note=pitch, velocity=vel, channel=ev_chan, time=delta))

        track.append(MetaMessage('end_of_track', time=0))
        total_notes_count += len(note_list)
        
        exported_tracks_info.append({
            "name": stem_name,
            "channel": channel + 1,
            "program": program,
            "note_count": len(note_list),
            "is_drum": is_drum
        })

    parent_dir = os.path.dirname(output_midi_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    mid.save(str(output_midi_path))
    return {
        "success": True,
        "midi_path": str(output_midi_path),
        "total_notes": total_notes_count,
        "tracks": exported_tracks_info,
        "bpm": bpm
    }


def save_single_track_midi(note_list, output_midi_path, track_name="Track", bpm=120.0, is_drum=False):
    """
    Exporta un archivo MIDI individual para un instrumento específico.
    """
    stems_dict = {track_name: note_list}
    return create_multitrack_midi(stems_dict, output_midi_path, bpm=bpm, song_title=track_name)
