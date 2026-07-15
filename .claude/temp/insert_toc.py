#!/usr/bin/env python
"""Insert a Word TOC field into the docx at the placeholder page."""
import sys, io
from lxml import etree
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml
import docx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

INPUT = r"d:\app\Emily\需求\Emily系统技术白皮书.docx"

doc = docx.Document(INPUT)
body = doc.element.body

# Find all <w:p> elements
p_elements = body.findall(qn("w:p"))

# P18 is the empty heading paragraph on TOC page (index 18 in paragraphs list)
p18 = p_elements[18]

def make_toc_paragraph():
    """Create a paragraph containing a Word TOC field."""
    p = parse_xml(f'<w:p {nsdecls("w")}></w:p>')

    # Run 1: fldChar begin
    r1 = parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>')
    p.append(r1)

    # Run 2: instrText with TOC directive
    # TOC \o "1-3" = use heading levels 1-3
    # \h = hyperlinks
    # \z = hide tab leader/page numbers in web layout
    # \u = use applied paragraph outline level
    toc_instr = r' TOC \o "1-3" \h \z \u '
    r2 = parse_xml(f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve">' + toc_instr + '</w:instrText></w:r>')
    p.append(r2)

    # Run 3: fldChar separate
    r3 = parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>')
    p.append(r3)

    # Run 4: placeholder text (shown until user updates the field in Word)
    r4 = parse_xml(f'<w:r {nsdecls("w")}><w:rPr><w:color w:val="808080"/></w:rPr><w:t xml:space="preserve">（打开文档后，右键此处选择"更新域"以生成目录）</w:t></w:r>')
    p.append(r4)

    # Run 5: fldChar end
    r5 = parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>')
    p.append(r5)

    return p

# Replace P18 with the TOC paragraph
toc_p = make_toc_paragraph()
body.replace(p18, toc_p)

doc.save(INPUT)
print("Done - TOC field inserted at P18.")
