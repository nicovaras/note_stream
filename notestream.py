# -*- coding: utf-8 -*-
import sys
import re
import mido
import math
from io import StringIO

# --- Drum Note Mapping (General MIDI) ---
GM_DRUM_MAP = {
    'Kick': 36,
    'Snare': 38,
    'Rim': 37,
    'TomL': 43,
    'TomM': 47,
    'TomH': 50,
    'HiHatC': 42,
    'HiHatP': 44,
    'HiHatO': 46,
    'Crash1': 49,
    'Crash2': 57,
    'RideC': 53,
    'RideE': 51,
    'Clap': 39,
}

# --- Base Note to MIDI Value Mapping ---
NOTE_VALUES = {
    'C': 0, 'B#': 0,
    'Db': 1, 'C#': 1,
    'D': 2,
    'Eb': 3, 'D#': 3,
    'E': 4, 'Fb': 4,
    'F': 5, 'E#': 5,
    'Gb': 6, 'F#': 6,
    'G': 7,
    'Ab': 8, 'G#': 8,
    'A': 9,
    'Bb': 10, 'A#': 10,
    'B': 11, 'Cb': 11,
}

def parse_pitch(note_str):
    match = re.match(r"([A-Ga-g][#b]?)(-?\d+)", note_str)
    if not match:
        raise ValueError(f"Invalid note format: '{note_str}'")
    pitch_class_str = match.group(1).capitalize()
    octave = int(match.group(2))
    if pitch_class_str not in NOTE_VALUES:
        raise ValueError(f"Invalid pitch class: '{pitch_class_str}'")
    note_value = NOTE_VALUES[pitch_class_str]
    midi_note = 12 * (octave + 1) + note_value
    if not 0 <= midi_note <= 127:
        raise ValueError(f"MIDI note out of range (0-127): {midi_note} for '{note_str}'")
    return midi_note

def parse_time_string(time_str):
    measure_str, beat_str = time_str.split(':')
    measure = int(measure_str)
    beat_float = float(beat_str)
    if measure < 1 or beat_float < 1.0:
        raise ValueError("Measure and beat must be >= 1")
    return measure, beat_float

def parse_duration_symbol(duration_sym):
    duration_map = {'w': 1.0, 'h': 0.5, 'q': 0.25, 'e': 0.125, 's': 0.0625, 't': 0.03125}
    dotted = False
    base_sym = duration_sym
    if duration_sym.endswith('.'):
        dotted = True
        base_sym = duration_sym[:-1]
    if base_sym not in duration_map:
        raise ValueError(f"Invalid duration symbol: '{duration_sym}'")
    multiplier = duration_map[base_sym]
    return multiplier, dotted

def parse_notation_file_content(file_content):
    metadata = {
        'bpm': 120,
        'time_sig_num': 4,
        'time_sig_den': 4
    }
    events = []
    
    content_stream = StringIO(file_content)

    for line_num, line in enumerate(content_stream, 1):
        line = line.strip()
        if not line or line.startswith('//'):
            continue

        if line.startswith('BPM:'):
            metadata['bpm'] = int(line.split(':')[1].strip())
            if metadata['bpm'] <= 0: raise ValueError("BPM must be positive")
            continue
        elif line.startswith('TIME_SIG:'):
            num_str, den_str = line.split(':')[1].strip().split('/')
            metadata['time_sig_num'] = int(num_str)
            metadata['time_sig_den'] = int(den_str)
            if metadata['time_sig_num'] <= 0 or metadata['time_sig_den'] <= 0:
                raise ValueError("Time signature numerator and denominator must be positive")
            continue

        parts = line.split(None, 1)
        if len(parts) != 2:
            print(f"Skipping malformed line {line_num}: '{line}'", file=sys.stderr)
            continue


        time_str, event_details = parts
        
        if '//' in event_details:
            event_details = event_details.split('//', 1)[0]
        event_details = event_details.strip()

        if not event_details:
            continue
        
        try:
            measure, beat_float = parse_time_string(time_str)
            instr_notes_part, duration_sym = event_details.rsplit(':', 1)
            duration_sym = duration_sym.strip()
            instrument, notes_str = instr_notes_part.split(':', 1)
            instrument = instrument.strip().upper()
            notes_str = notes_str.strip()

            if instrument not in ('B', 'D', 'G', 'K'): 
                raise ValueError(f"Unknown instrument '{instrument}'")

            note_name_list = []
            if notes_str.startswith('(') and notes_str.endswith(')'):
                note_name_list = notes_str[1:-1].split()
                if not note_name_list: raise ValueError("Empty note parentheses.")
            elif notes_str:
                note_name_list = [notes_str]
            else:
                raise ValueError("No notes specified.")

            multiplier, dotted = parse_duration_symbol(duration_sym)

            for note_name in note_name_list:
                note_name = note_name.strip()
                if not note_name: continue

                midi_val = -1
                if instrument in ('B', 'G', 'K'): 
                    midi_val = parse_pitch(note_name)
                elif instrument == 'D':
                    drum_key = next((k for k in GM_DRUM_MAP if k.lower() == note_name.lower()), None)
                    if drum_key:
                        midi_val = GM_DRUM_MAP[drum_key]
                    else:
                        raise ValueError(f"Unknown drum name: '{note_name}'")
                
                events.append({
                    'measure': measure,
                    'beat': beat_float,
                    'instrument': instrument,
                    'note_name': note_name, 
                    'midi_note': midi_val,
                    'duration_symbol': duration_sym,
                })
        except ValueError as e:
            print(f"Skipping line {line_num} due to error: {e} in '{line}'", file=sys.stderr)

    events.sort(key=lambda x: (x['measure'], x['beat']))
    return metadata, events

def generate_midi_from_events(metadata, events, output_filename, ticks_per_beat=480):
    midi_file = mido.MidiFile(ticks_per_beat=ticks_per_beat, type=1)
    
    track_map = {}
    CHANNEL_MAP = {
        'B': 0, # Bass
        'D': 9, # Drums
        'G': 1, # Guitar
        'K': 2  # Keyboard
    }
    PROGRAM_MAP = {
        'B': 34, # Electric Bass (finger)
        'G': 29, # Overdriven Guitar
        'K': 0  # Acoustic Grand Piano for Keyboard
    }

    used_instruments = set(event['instrument'] for event in events)
    instrument_labels = {'B': 'Bass', 'D': 'Drums', 'G': 'Guitar', 'K': 'Keyboard'}

    for inst_code in ['B', 'D', 'G', 'K']: 
        if inst_code in used_instruments:
            track_map[inst_code] = midi_file.add_track(instrument_labels.get(inst_code, "Instrument"))


    microseconds_per_beat = mido.bpm2tempo(metadata['bpm'])
    time_sig_meta = mido.MetaMessage('time_signature',
                                     numerator=metadata['time_sig_num'],
                                     denominator=metadata['time_sig_den'],
                                     clocks_per_click=24,
                                     notated_32nd_notes_per_beat=8,
                                     time=0)
    tempo_meta = mido.MetaMessage('set_tempo', tempo=microseconds_per_beat, time=0)

    for inst_code in track_map.keys(): 
        track = track_map[inst_code]
        track.append(tempo_meta) 
        track.append(time_sig_meta) 
        
        if inst_code != 'D' and inst_code in PROGRAM_MAP: 
            track.append(mido.Message('program_change', 
                                      channel=CHANNEL_MAP[inst_code], 
                                      program=PROGRAM_MAP[inst_code], 
                                      time=0))
    
    timed_midi_messages_for_tracks = {key: [] for key in track_map.keys()}
    default_velocity = 100

    for event in events:
        if event['instrument'] not in track_map:
            continue

        ticks_for_one_signature_beat = ticks_per_beat * (4.0 / metadata['time_sig_den'])
        
        measure_start_ticks = (event['measure'] - 1) * metadata['time_sig_num'] * ticks_for_one_signature_beat
        beat_offset_ticks = (event['beat'] - 1) * ticks_for_one_signature_beat
        start_tick = round(measure_start_ticks + beat_offset_ticks)

        multiplier, dotted = parse_duration_symbol(event['duration_symbol'])
        duration_in_quarters = multiplier * 4.0 
        duration_ticks = duration_in_quarters * ticks_per_beat 
        if dotted:
            duration_ticks *= 1.5
        duration_ticks = round(duration_ticks)
        duration_ticks = max(1, duration_ticks) 

        end_tick = start_tick + duration_ticks

        note = event['midi_note']
        velocity = default_velocity
        instrument_key = event['instrument']
        channel = CHANNEL_MAP[instrument_key]
        
        timed_midi_messages_for_tracks[instrument_key].append(
            (start_tick, mido.Message('note_on', channel=channel, note=note, velocity=velocity, time=0))
        )
        timed_midi_messages_for_tracks[instrument_key].append(
            (end_tick, mido.Message('note_off', channel=channel, note=note, velocity=0, time=0))
        )

    for inst_key in track_map.keys():
        track_messages = timed_midi_messages_for_tracks[inst_key]
        track_messages.sort(key=lambda x: x[0]) 

        current_track_tick = 0
        for absolute_tick, message in track_messages:
            delta_time = absolute_tick - current_track_tick
            message.time = delta_time
            track_map[inst_key].append(message)
            current_track_tick = absolute_tick
            
    midi_file.save(output_filename)

if __name__ == "__main__":
    if len(sys.argv) != 3: 
        print(f"Usage: python {sys.argv[0]} <input_notation_file.txt> <output_midi_file.mid>", file=sys.stderr)
        sys.exit(1)

    input_notation_file = sys.argv[1]
    output_midi_file = sys.argv[2]

    try:
        with open(input_notation_file, 'r', encoding='utf-8') as f:
            notation_content = f.read()
    except FileNotFoundError:
        print(f"Error: Input file '{input_notation_file}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading input file '{input_notation_file}': {e}", file=sys.stderr)
        sys.exit(1)

    if not notation_content.strip():
        print("Error: Input file is empty or contains only whitespace.", file=sys.stderr)
        sys.exit(1)
        
    metadata, parsed_events = parse_notation_file_content(notation_content)

    if parsed_events is None:
        print("Parsing failed, no MIDI file will be generated.", file=sys.stderr)
        sys.exit(1)

    if not parsed_events:
        print("Warning: No valid musical events found in the input. Generating a MIDI file with metadata only.", file=sys.stderr)
    
    try:
        generate_midi_from_events(metadata, parsed_events, output_midi_file)
        print(f"MIDI file '{output_midi_file}' generated successfully.") 
    except Exception as e:
        print(f"Fatal error during MIDI generation: {e}", file=sys.stderr)
        sys.exit(1)