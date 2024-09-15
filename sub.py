import json

import datetime


def microseconds_to_timestamp(microseconds):
    td = datetime.timedelta(microseconds=microseconds)
    return str(td)[:-3].replace(".", ",")


fragments = json.load(open("data/f.json"))

cumulative_offset = 0
final_subtitles = []
fragment_durations = []

for fragment in fragments:
    fragment_end_time = max(offset + duration for (offset, duration), _ in fragment)
    fragment_durations.append(fragment_end_time)

    for (offset, duration), text in fragment:
        adjusted_offset = offset + cumulative_offset
        start_time = microseconds_to_timestamp(adjusted_offset)
        end_time = microseconds_to_timestamp(adjusted_offset + duration)
        final_subtitles.append((start_time, end_time, text))

    cumulative_offset += fragment_end_time


final_subtitles.sort(key=lambda x: x[0])

with open("output.srt", "w") as f:
    for i, (start_time, end_time, text) in enumerate(final_subtitles, 1):
        f.write(f"{i}\n{start_time} --> {end_time}\n{text}\n\n")
