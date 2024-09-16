import json

import edge_tts


def stitch_fragments_to_vtt(fragments):
    sub_maker = edge_tts.SubMaker()
    for fragment in fragments:
        for (start, duration), text in fragment:
            sub_maker.create_sub((start, duration), text)

    return sub_maker.generate_subs(words_in_cue=1)


# Read fragments from the JSON file
with open("data/f.json", "r") as file:
    fragments = json.load(file)

for fi in range(1, len(fragments)):
    (offset, duration), _ = fragments[fi - 1][-1]
    extra_offset = 13 * 1e6
    carry_offset = offset + duration + extra_offset
    for i in range(len(fragments[fi])):
        (start, duration), text = fragments[fi][i]
        fragments[fi][i] = ((start + carry_offset, duration), text)

# Generate VTT output from the fragments
vtt_output = stitch_fragments_to_vtt(fragments)

# Save the VTT to a file
with open("data/epub/1984-2.vtt", "w") as f:
    f.write(vtt_output)
