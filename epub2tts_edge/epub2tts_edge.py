import argparse
import asyncio
import concurrent.futures
import datetime
import os
import re
import subprocess
import time
import warnings
from tqdm import tqdm


from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub
import edge_tts
from lxml import etree
from mutagen import mp4
import nltk
from nltk.tokenize import sent_tokenize
from PIL import Image
from pydub import AudioSegment
import zipfile


namespaces = {
    "calibre": "http://calibre.kovidgoyal.net/2009/metadata",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "opf": "http://www.idpf.org/2007/opf",
    "u": "urn:oasis:names:tc:opendocument:xmlns:container",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

warnings.filterwarnings("ignore", module="ebooklib.epub")


def microseconds_to_timestamp(microseconds):
    td = datetime.timedelta(microseconds=microseconds)
    return str(td)[:-3].replace(".", ",")


def ensure_punkt():
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        nltk.download("punkt")

    nltk.download("punkt_tab")


def chap2text_epub(chap):
    blacklist = [
        "[document]",
        "noscript",
        "header",
        "html",
        "meta",
        "head",
        "input",
        "script",
    ]
    paragraphs = []
    soup = BeautifulSoup(chap, "html.parser")

    # Extract chapter title (assuming it's in an <h1> tag)
    chapter_title = soup.find("h1")
    if chapter_title:
        chapter_title_text = chapter_title.text.strip()
    else:
        chapter_title_text = None

    # Always skip reading links that are just a number (footnotes)
    for a in soup.findAll("a", href=True):
        if not any(char.isalpha() for char in a.text):
            a.extract()

    chapter_paragraphs = soup.find_all("p")
    for p in chapter_paragraphs:
        paragraph_text = "".join(p.strings).strip()
        paragraphs.append(paragraph_text)

    return chapter_title_text, paragraphs


def get_epub_cover(epub_path):
    try:
        with zipfile.ZipFile(epub_path) as z:
            t = etree.fromstring(z.read("META-INF/container.xml"))
            rootfile_path = t.xpath(
                "/u:container/u:rootfiles/u:rootfile", namespaces=namespaces
            )[0].get("full-path")

            t = etree.fromstring(z.read(rootfile_path))
            cover_meta = t.xpath(
                "//opf:metadata/opf:meta[@name='cover']", namespaces=namespaces
            )
            if not cover_meta:
                print("No cover image found.")
                return None
            cover_id = cover_meta[0].get("content")

            cover_item = t.xpath(
                "//opf:manifest/opf:item[@id='" + cover_id + "']", namespaces=namespaces
            )
            if not cover_item:
                print("No cover image found.")
                return None
            cover_href = cover_item[0].get("href")
            cover_path = os.path.join(os.path.dirname(rootfile_path), cover_href)

            return z.open(cover_path)
    except FileNotFoundError:
        print(f"Could not get cover image of {epub_path}")


def export_chapters(book, sourcefile):
    # TODO: Add support for from_chapter and to_chapter
    book_contents = []
    cover_image = get_epub_cover(sourcefile)
    cover_image_path = None

    if cover_image is not None:
        image = Image.open(cover_image)
        image_filename = sourcefile.replace(".epub", ".png")
        cover_image_path = os.path.join(image_filename)
        image.save(cover_image_path)
        print(f"Cover image saved to {cover_image_path}")

    spine_ids = []
    for spine_tuple in book.spine:
        if spine_tuple[1] == "yes":  # if item in spine is linear
            spine_ids.append(spine_tuple[0])

    items = {}
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            items[item.get_id()] = item

    for id in spine_ids:
        item = items.get(id, None)
        if item is None:
            continue
        chapter_title, chapter_paragraphs = chap2text_epub(item.get_content())
        book_contents.append({"title": chapter_title, "paragraphs": chapter_paragraphs})

    chapters = []
    for i, chapter in enumerate(book_contents, start=1):
        outfile = f"{sourcefile.replace('.epub', '')}-{i}.txt"
        print(f"Exporting {sourcefile} to {outfile}")
        if chapter["paragraphs"] == [] or chapter["paragraphs"] == [""]:
            print(f"Skipping empty chapter {i}")
            continue
        with open(outfile, "w") as file:
            if chapter["title"]:
                file.write(f"# {chapter['title']}\n\n")
            for paragraph in chapter["paragraphs"]:
                clean = re.sub(r"[\s\n©]+", " ", paragraph)
                file.write(f"{clean}\n")
        chapters.append(outfile)

    return chapters, cover_image_path


def get_book(sourcefile):
    book_contents = []
    book_title = sourcefile
    book_author = "Unknown"
    chapter_titles = []

    with open(sourcefile, "r", encoding="utf-8") as file:
        current_chapter = {"title": "blank", "paragraphs": []}
        initialized_first_chapter = False
        lines_skipped = 0
        for line in file:
            if lines_skipped < 2 and (
                line.startswith("Title") or line.startswith("Author")
            ):
                lines_skipped += 1
                if line.startswith("Title: "):
                    book_title = line.replace("Title: ", "").strip()
                elif line.startswith("Author: "):
                    book_author = line.replace("Author: ", "").strip()
                continue
            line = line.strip()
            if line.startswith("#"):
                if current_chapter["paragraphs"] or not initialized_first_chapter:
                    if initialized_first_chapter:
                        book_contents.append(current_chapter)
                    current_chapter = {"title": None, "paragraphs": []}
                    initialized_first_chapter = True
                chapter_title = line[1:].strip()
                if any(c.isalnum() for c in chapter_title):
                    current_chapter["title"] = chapter_title
                    chapter_titles.append(current_chapter["title"])
                else:
                    current_chapter["title"] = "blank"
                    chapter_titles.append("blank")
            elif line:
                if not initialized_first_chapter:
                    chapter_titles.append("blank")
                    initialized_first_chapter = True
                if any(char.isalnum() for char in line):
                    sentences = sent_tokenize(line)
                    cleaned_sentences = [
                        s for s in sentences if any(char.isalnum() for char in s)
                    ]
                    line = " ".join(cleaned_sentences)
                    current_chapter["paragraphs"].append(line)

        # Append the last chapter if it contains any paragraphs.
        if current_chapter["paragraphs"]:
            book_contents.append(current_chapter)

    return book_contents, book_title, book_author, chapter_titles


def sort_key(s):
    # extract number from the string
    return int(re.findall(r"\d+", s)[0])


def append_silence(tempfile, duration=1200):
    audio = AudioSegment.from_file(tempfile)
    # Create a silence segment
    silence = AudioSegment.silent(duration)
    # Append the silence segment to the audio
    combined = audio + silence
    # Save the combined audio back to file
    combined.export(tempfile, format="flac")


def read_book(sourcefile, book_contents, speaker):
    segments = []
    subs_fragments = []
    basefile = sourcefile.replace(".txt", "")
    for i, chapter in enumerate(book_contents, start=1):
        files = []
        partname = f"{basefile}-part{i}.flac"

        if os.path.isfile(partname):
            print(f"{partname} exists, skipping to next chapter")
            segments.append(partname)
        else:
            print(f"Chapter: {chapter['title']}\n")
            if chapter["title"] == "":
                chapter["title"] = "blank"
            subs_fragments += asyncio.run(
                parallel_edgespeak(
                    [chapter["title"]], [speaker], [f"{basefile}-sntnc0.mp3"]
                )
            )
            append_silence(f"{basefile}-sntnc0.mp3", 1200)
            for pindex, paragraph in enumerate(
                tqdm(
                    chapter["paragraphs"],
                    desc=f"Processing chapter {sourcefile}",
                    unit="pg",
                )
            ):
                ptemp = f"{basefile}-pgraphs{pindex}.flac"
                if os.path.isfile(ptemp):
                    print(f"{ptemp} exists, skipping to next paragraph")
                else:
                    sentences = sent_tokenize(paragraph)
                    filenames = [
                        f"{basefile}-sntnc" + str(z + 1) + ".mp3"
                        for z in range(len(sentences))
                    ]
                    speakers = [speaker] * len(sentences)
                    subs_fragments += asyncio.run(
                        parallel_edgespeak(sentences, speakers, filenames)
                    )
                    append_silence(filenames[-1], 1200)
                    # combine sentences in paragraph
                    sorted_files = sorted(filenames, key=sort_key)
                    if os.path.exists(f"{basefile}-sntnc0.mp3"):
                        sorted_files.insert(0, f"{basefile}-sntnc0.mp3")
                    combined = AudioSegment.empty()
                    for file in sorted_files:
                        combined += AudioSegment.from_file(file)
                    combined.export(ptemp, format="flac")
                    for file in sorted_files:
                        os.remove(file)
                files.append(ptemp)
            # combine paragraphs into chapter
            append_silence(files[-1], 2800)
            combined = AudioSegment.empty()
            for file in files:
                combined += AudioSegment.from_file(file)
            combined.export(partname, format="flac")
            for file in files:
                os.remove(file)
            segments.append(partname)

        # Generate subtitles

        cumulative_offset = 0
        final_subtitles = []
        fragment_durations = []

        for fragment in subs_fragments:
            fragment_end_time = max(
                offset + duration for (offset, duration), _ in fragment
            )
            fragment_durations.append(fragment_end_time)

            for (offset, duration), text in fragment:
                adjusted_offset = offset + cumulative_offset
                start_time = microseconds_to_timestamp(adjusted_offset)
                end_time = microseconds_to_timestamp(adjusted_offset + duration)
                final_subtitles.append((start_time, end_time, text))

            cumulative_offset += fragment_end_time

        final_subtitles.sort(key=lambda x: x[0])

        subtitle_file = f"{basefile}.vtt"
        print(f"Writing subtitles to {subtitle_file}")
        with open(subtitle_file, "w") as f:
            f.write("WEBVTT\n\n")
            for i, (start_time, end_time, text) in enumerate(final_subtitles, 1):
                f.write(f"{start_time} --> {end_time}\n{text}\n\n")

    return segments


def generate_metadata(sourcefile, files, author, title, chapter_titles):
    # TODO: Fix the metadata properties here
    chap = 0
    start_time = 0
    ffmetadatafile = sourcefile.replace(".txt", ".ffmetadata")
    with open(ffmetadatafile, "w") as file:
        file.write(";FFMETADATA1\n")
        file.write(f"ARTIST={author}\n")
        file.write(f"ALBUM={title}\n")
        for file_name in files:
            duration = get_duration(file_name)
            file.write("[CHAPTER]\n")
            file.write("TIMEBASE=1/1000\n")
            file.write(f"START={start_time}\n")
            file.write(f"END={start_time + duration}\n")
            file.write(f"title={chapter_titles[chap]}\n")
            chap += 1
            start_time += duration

    return ffmetadatafile


def get_duration(file_path):
    audio = AudioSegment.from_file(file_path)
    duration_milliseconds = len(audio)
    return duration_milliseconds


def make_m4b(files, sourcefile, speaker, ffmetadatafile):
    filelist = sourcefile.split("/")[-1] + ".txt"
    basefile = sourcefile.replace(".txt", "")
    outputm4a = f"{basefile}.m4a"
    outputm4b = f"{basefile}.m4b"
    with open(filelist, "w") as f:
        for filename in files:
            filename = filename.replace("'", "'\\''")
            f.write(f"file '{filename}'\n")
    ffmpeg_command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        filelist,
        "-codec:a",
        "flac",
        "-f",
        "mp4",
        "-strict",
        "-2",
        outputm4a,
    ]
    subprocess.run(ffmpeg_command)
    ffmpeg_command = [
        "ffmpeg",
        "-y",
        "-i",
        outputm4a,
        "-i",
        ffmetadatafile,
        "-map_metadata",
        "1",
        "-codec",
        "aac",
        outputm4b,
    ]
    subprocess.run(ffmpeg_command)
    os.remove(filelist)
    os.remove(ffmetadatafile)
    os.remove(outputm4a)
    for f in files:
        os.remove(f)
    return outputm4b


def add_cover(cover_img, filename):
    # TODO: add metadata to m4b file https://www.audiobookshelf.org/docs#book-audio-metadata
    try:
        if os.path.isfile(cover_img):
            m4b = mp4.MP4(filename)
            cover_image = open(cover_img, "rb").read()
            m4b["covr"] = [mp4.MP4Cover(cover_image)]
            m4b.save()
        else:
            print(f"Cover image {cover_img} not found")
    except:
        print(f"Cover image {cover_img} not found")


def run_edgespeak(sentence, speaker, filename):
    for speakattempt in range(3):
        try:
            communicate = edge_tts.Communicate(sentence, speaker)
            subs = asyncio.run(
                run_tts(
                    communicate=communicate,
                    filename=filename,
                )
            )
            if os.path.getsize(filename) == 0:
                raise Exception("Failed to save file from edge_tts") from e
            return subs
        except Exception as e:
            print(
                f"Attempt {speakattempt+1}/3 failed with '{sentence}' in run_edgespeak with error: {e}"
            )
            # wait a few seconds in case its a transient network issue
            time.sleep(3)
    else:
        print(f"Giving up on sentence '{sentence}' after 3 attempts in run_edgespeak.")
        exit()


async def run_tts(communicate, filename):
    subs = []
    with open(filename, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                subs.append(((chunk["offset"], chunk["duration"]), chunk["text"]))
    return subs


async def parallel_edgespeak(sentences, speakers, filenames):
    semaphore = asyncio.Semaphore(10)  # Limit the number of concurrent tasks

    with concurrent.futures.ThreadPoolExecutor() as executor:
        tasks = []
        for sentence, speaker, filename in zip(sentences, speakers, filenames):
            async with semaphore:
                loop = asyncio.get_running_loop()
                sentence = re.sub(r"[!]+", "!", sentence)
                sentence = re.sub(r"[?]+", "?", sentence)
                task = loop.run_in_executor(
                    executor, run_edgespeak, sentence, speaker, filename
                )
                tasks.append(task)
        return await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser(
        prog="epub2tts-edge",
        description="Read a text file to audiobook format",
    )
    parser.add_argument("sourcefile", type=str, help="The epub or text file to process")
    parser.add_argument(
        "--speaker",
        type=str,
        nargs="?",
        const="en-US-AndrewNeural",
        default="en-US-AndrewNeural",
        help="Speaker to use (ex en-US-MichelleNeural)",
    )
    parser.add_argument(
        "--cover",
        type=str,
        help="jpg image to use for cover",
    )

    args = parser.parse_args()
    print(args)

    ensure_punkt()

    # If we get an epub, export that to txt file, then exit
    if args.sourcefile.endswith(".epub"):
        book = epub.read_epub(args.sourcefile)
        export_chapters(book, args.sourcefile)
        exit()

    book_contents, book_title, book_author, chapter_titles = get_book(args.sourcefile)
    files = read_book(
        sourcefile=args.sourcefile, book_contents=book_contents, speaker=args.speaker
    )
    ffmetadatafile = generate_metadata(
        sourcefile=args.sourcefile,
        files=files,
        author=book_author,
        title=book_title,
        chapter_titles=chapter_titles,
    )
    m4bfilename = make_m4b(
        files=files,
        sourcefile=args.sourcefile,
        speaker=args.speaker,
        ffmetadatafile=ffmetadatafile,
    )
    add_cover(args.cover, m4bfilename)


if __name__ == "__main__":
    main()
