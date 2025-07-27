import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import click
import fitz
import pdfplumber
from collections import defaultdict, Counter


class PDFStructureExtractor:
    def __init__(self):
        self.font_analysis = {}
        
        # Common document headers/footers to ignore
        self.ignore_patterns = [
            r'^International\s+Software\s+Testing.*Board$',
            r'^©.*Software Testing.*Board$',
            r'^Version\s+\d{4}.*Page\s+\d+',
            r'^Board$',
            r'^Qualifications\s+Board$',
            r'^\s*Page\s+\d+\s*of\s*\d+',
            r'^\s*\d+\s*$',  # Just page numbers
        ]
        
        # Heading patterns in priority order
        self.heading_patterns = [
            # Very strong indicators
            (r'^Chapter\s+\d+[\.:]\s*(.+)', 'chapter', 1),
            (r'^Section\s+\d+[\.:]\s*(.+)', 'section', 1),
            (r'^Part\s+\d+[\.:]\s*(.+)', 'part', 1),
            (r'^Appendix\s+[A-Z][\.:]\s*(.+)', 'appendix', 1),
            
            # Numbered sections
            (r'^(\d+)\.\s+([A-Z].+)$', 'numbered_main', 1),
            (r'^(\d+\.\d+)\s+([A-Z].+)$', 'numbered_sub', 2),
            (r'^(\d+\.\d+\.\d+)\s+([A-Z].+)$', 'numbered_subsub', 3),
            
            # Special sections
            (r'^(Executive\s+Summary|Abstract|Introduction|Conclusion|References|Bibliography|Acknowledgements?)$', 'special', 1),
            (r'^(Table\s+of\s+Contents|Revision\s+History|Glossary|Index)$', 'special', 1),
            
            # All caps (but not too short)
            (r'^[A-Z][A-Z\s\-&]{4,}$', 'allcaps', 2),
            
            # Title case 
            (r'^[A-Z][a-z]+(\s+[A-Z][a-z]+)*:?$', 'titlecase', 3),
        ]
        
        # Form field indicators
        self.form_indicators = [
            r'^\d+\.\s*(Name|Date|Designation|Address|Phone|Email|Age|Gender)\b',
            r'\b(Name|Date|Amount|Address|Signature)\s*:?\s*$',
            r'.*\.\.\.\s*$',
            r'.*___+\s*$',
            r'\bRs\.\s*\d*\s*$',
            r'^Whether\s+',
            r'^\s*\d+\.\s*$',  # Just numbers with period
        ]
    
    def analyze_document_structure(self, pdf_path: str) -> Dict[str, Any]:
        """Analyze PDF structure and extract font statistics and text blocks."""
        doc = fitz.open(pdf_path)
        
        font_stats = defaultdict(lambda: {'count': 0, 'total_chars': 0, 'blocks': []})
        text_blocks = []
        page_count = len(doc)
        
        for page_num in range(page_count):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]
            
            for block in blocks:
                if "lines" in block:
                    block_info = self._process_text_block(block, page_num)
                    if block_info:
                        text_blocks.append(block_info)
                        
                        # Track font usage
                        font_key = (round(block_info["font_size"], 1), block_info["flags"])
                        font_stats[font_key]['count'] += 1
                        font_stats[font_key]['total_chars'] += block_info["length"]
                        font_stats[font_key]['blocks'].append(block_info["text"][:50])
        
        doc.close()
        
        # Analyze fonts
        font_analysis = self._analyze_fonts(font_stats)
        
        return {
            "text_blocks": text_blocks,
            "font_analysis": font_analysis,
            "page_count": page_count
        }
    
    def _process_text_block(self, block: Dict, page_num: int) -> Optional[Dict]:
        """Process a single text block and extract its properties."""
        block_text = ""
        font_sizes = []
        font_flags = []
        
        for line in block["lines"]:
            line_text = ""
            for span in line["spans"]:
                text = span["text"].strip()
                if text:
                    line_text += text + " "
                    font_sizes.append(span["size"])
                    font_flags.append(span["flags"])
            
            if line_text.strip():
                block_text += line_text.strip() + " "
        
        block_text = block_text.strip()
        
        # Skip empty or very short blocks
        if not block_text or len(block_text) < 2:
            return None
        
        # Skip if matches ignore patterns
        if any(re.match(pattern, block_text) for pattern in self.ignore_patterns):
            return None
        
        avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12
        most_common_flag = Counter(font_flags).most_common(1)[0][0] if font_flags else 0
        
        return {
            "text": block_text,
            "page": page_num + 1,
            "font_size": avg_font_size,
            "flags": most_common_flag,
            "length": len(block_text),
            "bbox": block.get("bbox", [0, 0, 0, 0]),
            "y_pos": block.get("bbox", [0, 0, 0, 0])[1]
        }
    
    def _analyze_fonts(self, font_stats: Dict) -> Dict[str, Any]:
        """Analyze font statistics to determine body text and heading fonts."""
        if not font_stats:
            return {"body_font_size": 12, "heading_fonts": []}
        
        # Find body font (most characters)
        body_font = max(font_stats.items(), key=lambda x: x[1]['total_chars'])[0]
        body_font_size = body_font[0]
        
        # Find potential heading fonts (larger than body)
        heading_fonts = []
        for font_key, stats in font_stats.items():
            font_size = font_key[0]
            if font_size > body_font_size and stats['count'] > 1:
                heading_fonts.append({
                    'size': font_size,
                    'flags': font_key[1],
                    'diff': font_size - body_font_size
                })
        
        heading_fonts.sort(key=lambda x: x['size'], reverse=True)
        
        return {
            "body_font_size": body_font_size,
            "heading_fonts": heading_fonts
        }
    
    def is_form_document(self, text_blocks: List[Dict]) -> bool:
        """Detect if this is primarily a form document."""
        if not text_blocks:
            return False
        
        # Check for form title
        for block in text_blocks[:10]:
            text_lower = block["text"].lower()
            if 'application form' in text_lower or 'form for' in text_lower:
                return True
        
        # Count form field indicators
        form_field_count = 0
        for block in text_blocks[:30]:  # Check first 30 blocks
            text = block["text"]
            if any(re.match(pattern, text, re.IGNORECASE) for pattern in self.form_indicators):
                form_field_count += 1
        
        # If more than 30% look like form fields, it's likely a form
        return form_field_count > len(text_blocks[:30]) * 0.3
    
    def extract_title(self, text_blocks: List[Dict], is_form: bool) -> str:
        """Extract the document title."""
        candidates = []
        
        # For forms, look for "form" in the text
        if is_form:
            for block in text_blocks[:20]:
                text = block["text"]
                if 'form' in text.lower() and len(text) < 100:
                    return text
        
        # Look for title candidates in first few blocks
        for i, block in enumerate(text_blocks[:30]):
            text = block["text"].strip()
            
            # Skip common non-titles
            if any(pattern in text.lower() for pattern in [
                'page ', 'version ', 'copyright', 'table of contents',
                'revision history', 'acknowledgements', 'www.', 'http'
            ]):
                continue
            
            # Skip dates
            if re.match(r'^[A-Z][a-z]+\s+\d+,\s+\d{4}$', text):
                continue
            
            # Skip if too short or too long
            if len(text) < 5 or len(text) > 200:
                continue
            
            # Score the candidate
            score = 0
            
            # Position score
            if i < 5:
                score += 5 - i
            
            # Font size score
            score += block["font_size"] / 10
            
            # Page 1 bonus
            if block["page"] == 1:
                score += 3
            
            # Upper page bonus
            if block["y_pos"] < 300:
                score += 2
            
            # Length preference
            if 10 <= len(text) <= 80:
                score += 2
            
            candidates.append((text, score, block))
        
        if not candidates:
            return "Document"
        
        # Sort by score
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        # For party invitations, check for specific patterns
        top_text = candidates[0][0]
        if any(word in top_text.upper() for word in ['PARTY', 'INVITED', 'INVITATION']):
            # Look for a better title that's not just a field
            for text, score, block in candidates:
                if 'INVITED' in text.upper() and 'YOU' in text.upper():
                    return text
                elif 'PARTY' in text.upper() and not text.endswith(':'):
                    return text
        
        return candidates[0][0]
    
    def is_heading(self, text: str, font_size: float, flags: int, 
                   body_font_size: float, is_form: bool) -> Tuple[bool, int]:
        """Determine if text is a heading and its level."""
        text = text.strip()
        
        # Never treat form fields as headings in form documents
        if is_form:
            return False, 0
        
        # Skip if matches ignore patterns
        if any(re.match(pattern, text) for pattern in self.ignore_patterns):
            return False, 0
        
        # Skip very long text
        if len(text) > 150:
            return False, 0
        
        # Skip dates
        if re.match(r'^[A-Z][a-z]+\s+\d+,\s+\d{4}$', text):
            return False, 0
        
        # Check font properties
        font_diff = font_size - body_font_size
        is_bold = bool(flags & 2**4)
        
        # Check heading patterns
        for pattern, pattern_type, default_level in self.heading_patterns:
            match = re.match(pattern, text)
            if match:
                # Determine level based on pattern and font
                if pattern_type in ['chapter', 'part', 'special']:
                    return True, 1
                elif pattern_type == 'numbered_main':
                    return True, 1
                elif pattern_type == 'numbered_sub':
                    return True, 2
                elif pattern_type == 'numbered_subsub':
                    return True, 3
                elif pattern_type == 'appendix':
                    # Check font to determine if H1 or H3
                    if font_diff >= 4:
                        return True, 1
                    else:
                        return True, 3
                elif pattern_type == 'allcaps':
                    # Use font size to determine level
                    if font_diff >= 4:
                        return True, 1
                    elif font_diff >= 2 or is_bold:
                        return True, 2
                    else:
                        return True, 3
                elif pattern_type == 'titlecase':
                    # Only consider if font is larger or bold
                    if font_diff >= 2 or is_bold:
                        if font_diff >= 4:
                            return True, 1
                        elif font_diff >= 2:
                            return True, 2
                        else:
                            return True, 3
        
        return False, 0
    
    def extract_outline(self, text_blocks: List[Dict], body_font_size: float, 
                       is_form: bool, title: str) -> List[Dict]:
        """Extract document outline."""
        outline = []
        seen_texts = set()
        
        # Don't extract outline for forms
        if is_form:
            return []
        
        for block in text_blocks:
            text = block["text"].strip()
            
            # Skip if we've seen this exact text before
            if text in seen_texts:
                continue
            
            # Skip if it's the same as the title
            if text == title:
                continue
            
            is_heading, level = self.is_heading(
                text, 
                block["font_size"], 
                block["flags"],
                body_font_size,
                is_form
            )
            
            if is_heading:
                outline_entry = {
                    "level": f"H{level}",
                    "text": text,
                    "page": block["page"]
                }
                outline.append(outline_entry)
                seen_texts.add(text)
        
        return outline
    
    def extract_pdf_data(self, pdf_path: str) -> Dict[str, Any]:
        """Main extraction method."""
        # Analyze document
        analysis = self.analyze_document_structure(pdf_path)
        text_blocks = analysis["text_blocks"]
        font_analysis = analysis["font_analysis"]
        
        if not text_blocks:
            return {"title": "Document", "outline": []}
        
        # Detect document type
        is_form = self.is_form_document(text_blocks)
        
        # Extract title
        title = self.extract_title(text_blocks, is_form)
        
        # Extract outline
        body_font_size = font_analysis["body_font_size"]
        outline = self.extract_outline(text_blocks, body_font_size, is_form, title)
        
        return {
            "title": title,
            "outline": outline
        }


@click.command()
@click.argument('pdf_path', type=click.Path(exists=True, path_type=Path))
@click.option('--output', '-o', type=click.Path(path_type=Path), 
              help='Output JSON file path (default: input_filename.json)')
@click.option('--pretty', '-p', is_flag=True, help='Pretty print JSON output')
@click.option('--debug', '-d', is_flag=True, help='Show debug information')
def main(pdf_path: Path, output: Optional[Path], pretty: bool, debug: bool):
    """Extract structured data from PDF files and output as JSON."""
    
    if not pdf_path.suffix.lower() == '.pdf':
        click.echo("Error: Input file must be a PDF", err=True)
        return
    
    if not output:
        output = pdf_path.with_suffix('.json')
    
    click.echo(f"Extracting data from: {pdf_path}")
    
    try:
        extractor = PDFStructureExtractor()
        extracted_data = extractor.extract_pdf_data(str(pdf_path))
        
        if debug:
            click.echo(f"Document title: {extracted_data['title']}")
            click.echo(f"Found {len(extracted_data['outline'])} headings")
            if len(extracted_data['outline']) > 0:
                click.echo("\nOutline preview:")
                for item in extracted_data['outline'][:5]:
                    click.echo(f"  {item['level']}: {item['text'][:50]}...")
        
        indent = 2 if pretty else None
        
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(extracted_data, f, indent=indent, ensure_ascii=False)
        
        click.echo(f"✓ Extraction complete! Output saved to: {output}")
        
    except Exception as e:
        click.echo(f"Error extracting PDF data: {e}", err=True)
        if debug:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()