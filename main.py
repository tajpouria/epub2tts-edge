import json
from ebooklib import epub
from concurrent.futures import ProcessPoolExecutor, as_completed

from epub2tts_edge import epub2tts_edge as e2t

speakers = json.load(open("speakers.json"))


def process_chapter(chapter_sourcefile, cover_image_path, speaker):
    book_contents, book_title, book_author, chapter_titles = e2t.get_book(
        sourcefile=chapter_sourcefile
    )

    files = e2t.read_book(
        sourcefile=chapter_sourcefile, book_contents=book_contents, speaker=speaker
    )

    ffmetadatafile = e2t.generate_metadata(
        sourcefile=chapter_sourcefile,
        files=files,
        author=book_author,
        title=book_title,
        chapter_titles=chapter_titles,
    )

    m4bfilename = e2t.make_m4b(
        files=files,
        sourcefile=chapter_sourcefile,
        speaker=speaker,
        ffmetadatafile=ffmetadatafile,
    )

    e2t.add_cover(cover_image_path, m4bfilename)


def main(
    sourcefile="data/epub/1984.epub",
    speaker="en-US-AndrewNeural",
    max_workers=4,
    from_chapter=1,
    to_chapter=4,
):
    e2t.ensure_punkt()

    book = epub.read_epub(sourcefile)
    chapters, cover_image_path = e2t.export_chapters(book=book, sourcefile=sourcefile)

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_chapter, chapter, cover_image_path, speaker)
            for chapter in chapters[
                max(1, from_chapter) - 1 : min(len(chapters), to_chapter)
            ]
        ]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"Error processing chapter: {e}")


if __name__ == "__main__":
    main()
