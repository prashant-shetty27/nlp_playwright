"""
ui/pages/data/media_library.py — Media Library  (route: /data/media)

Central store for images, PDFs, and file attachments used in test flows.
Shared across Web, Android, and iOS platforms.

Use cases:
    - Upload a file to test file-upload flows (e.g. "upload file 'invoice.pdf'")
    - Store reference screenshots for visual comparison steps
    - Keep PDF documents for download-verification tests

Layout:
    Toolbar
        [Upload] button → drag-and-drop or file picker
        Filter: All | Images | PDFs | Other
        Search by filename

    Media grid (thumbnail view)
        Image files: thumbnail preview
        PDF files: PDF icon + page count
        Others: generic file icon + extension badge
        Each item: filename | size | upload date | [Use in flow] | [Delete]

    File detail panel (side drawer)
        Full preview (image zoom / PDF page viewer)
        File metadata: dimensions, MIME type, size
        Path in project: data/common/media/<filename>
        NLP reference: how to reference this file in a flow step
            e.g. 'upload file "invoice.pdf"' → resolved to data/common/media/invoice.pdf

Storage: data/common/media/
"""
