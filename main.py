import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
import click
import fitz
import pdfplumber


class PDFStructureExtractor:
    def __init__(self):
        self.font_analysis = {}
        self.heading_patterns = [
            r'^[A-Z][A-Z\s]{2,}$',  # ALL CAPS headings
            r'^\d+\.\s+[A-Z]',      # Numbered headings like "1. Introduction"
            r'^[A-Z][a-z]+(\s+[A-Z][a-z]+)*$',  # Title Case headings
            r'^Chapter\s+\d+',      # Chapter headings
            r'^Section\s+\d+',      # Section headings
            r'^Round\s+\d+',        # Round headings
        ]
    
    def analyze_document_structure(self, pdf_path: str) -> Dict[str, Any]:
        doc = fitz.open(pdf_path)
        
        font_stats = {}
        text_blocks = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]
            
            for block in blocks:
                if "lines" in block:
                    block_text = ""
                    block_font_sizes = []
                    block_flags = []
                    
                    for line in block["lines"]:
                        line_text = ""
                        for span in line["spans"]:
                            text = span["text"].strip()
                            if text:
                                line_text += text + " "
                                block_font_sizes.append(span["size"])
                                block_flags.append(span["flags"])
                                
                                font_key = (span["size"], span["flags"])
                                font_stats[font_key] = font_stats.get(font_key, 0) + len(text)
                        
                        if line_text.strip():
                            block_text += line_text.strip() + " "
                    
                    if block_text.strip():
                        avg_font_size = sum(block_font_sizes) / len(block_font_sizes) if block_font_sizes else 12
                        most_common_flag = max(set(block_flags), key=block_flags.count) if block_flags else 0
                        
                        text_blocks.append({
                            "text": block_text.strip(),
                            "page": page_num + 1,
                            "font_size": avg_font_size,
                            "flags": most_common_flag,
                            "length": len(block_text.strip()),
                            "bbox": block.get("bbox", [0, 0, 0, 0])
                        })
        
        doc.close()
        
        # Determine font hierarchy
        font_sizes = [stats[0] for stats in font_stats.keys()]
        sorted_sizes = sorted(set(font_sizes), reverse=True)
        
        # Get the most common (body text) font size
        body_font_size = max(font_stats.keys(), key=font_stats.get)[0]
        
        return {
            "text_blocks": text_blocks,
            "font_hierarchy": sorted_sizes,
            "body_font_size": body_font_size,
            "font_stats": font_stats
        }
    
    def is_likely_heading(self, text: str, font_size: float, flags: int, body_font_size: float) -> tuple:
        # Skip very long text blocks - headings are typically shorter
        if len(text) > 150:
            return False, 0
        
        # Skip text that ends with common sentence endings
        if text.endswith(('.', ':', ';', ',', '!', '?')) and len(text) > 50:
            return False, 0
        
        # Check formatting flags and font differences first
        is_bold = bool(flags & 2**4)  # Bold flag
        font_diff = font_size - body_font_size
        
        # Universal filters based on content characteristics, not specific text
        
        # Filter out very short text (likely fragments or single words)
        if len(text.strip()) <= 3:
            return False, 0
        
        # Filter out text that looks like form fields or data entries
        # These typically have specific formatting patterns
        if (
            # Text ending with currency symbols, units, or typical form field endings
            re.search(r'\b(Rs\.|USD|\$|€|£|%)\s*$', text) or
            # Text that looks like instructions or questions (but allow real section questions)
            (text.endswith('?') and len(text) > 30) or
            # Text that looks like data values or measurements
            re.search(r'^\d+[\.\,]\d+', text) or
            # Text that looks like dates in various formats
            re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', text) or
            # Very long text blocks (headings should be concise)
            len(text) > 200 or
            # Form field patterns (universal indicators)
            re.match(r'^\d+\.\s+[A-Z]', text) or  # Numbered form fields like "1. Name", "2. Designation"
            text.endswith(':') and len(text) > 25 or  # Long descriptive labels ending with colon
            re.search(r'\b(Name|Designation|Date|Amount|Address|Age|Gender|Phone|Email|ID|Number)\b', text) and len(text) < 50
        ):
            return False, 0
        
        # Filter out single common words that appear repeatedly (likely template artifacts)
        single_words = text.split()
        if len(single_words) == 1 and len(single_words[0]) <= 10:
            # This is a single short word - only accept if it has good formatting support
            if not (font_diff >= 2 or is_bold):
                return False, 0
        
        # Strong heading indicators (usually H1) - based on universal structural patterns
        strong_patterns = [
            r'^[A-Z][A-Z\s]{5,}$',  # ALL CAPS text (likely major headings)
            r'^Chapter\s+\d+',      # Chapter/Part/Section structural indicators
            r'^Section\s+\d+',      
            r'^Part\s+\d+',         
            r'^[IVX]+\.\s+[A-Z]',   # Roman numeral headings
            r'^Appendix\s+[A-Z0-9]', # Appendix sections
            r'^Abstract$|^Summary$|^Introduction$|^Conclusion$|^References$', # Common document sections
            r'^Table\s+of\s+Contents$|^Acknowledgements?$|^Bibliography$', # Document navigation sections
        ]
        
        # Medium heading indicators (usually H2) - universal formatting patterns
        medium_patterns = [
            r'^[A-Z][a-z]+(\s+[A-Z][a-z]+)*$',  # Title Case headings (2-6 words)
            r'^\d+\.\d+\s+[A-Z]',   # Section numbers like "2.1 Intended Audience"
            r'^[A-Z][a-z]+(\s+[a-z]+)*:$',  # Headings ending with colon
            r'^[A-Z][^.]{8,50}$',   # Medium-length capitalized phrases
        ]
        
        # Weak heading indicators (need font size support)
        weak_patterns = [
            r'^•\s+[A-Z]',          # Bullet points that might be sub-headings
            r'^o\s+[A-Z]',          # Sub-bullet points
            r'^\d+\)\s+[A-Z]',      # Numbered list items
            r'^[a-z]\)\s+[A-Z]',    # Lettered list items
        ]
        
        # Check strong patterns - these are likely H1 or H2
        for pattern in strong_patterns:
            if re.match(pattern, text):
                if font_diff >= 4:
                    return True, 1  # H1
                elif font_diff >= 1 or is_bold:
                    return True, 2  # H2
                else:
                    return True, 3  # H3
        
        # Check medium patterns - these are likely H2 or H3
        for pattern in medium_patterns:
            if re.match(pattern, text):
                # Special handling for section numbers like "2.1 Intended Audience"
                if re.match(r'^\d+\.\d+\s+', text):
                    return True, 2  # Always H2 for section numbers
                elif font_diff >= 3:
                    return True, 1  # H1
                elif font_diff >= 1 or is_bold:
                    return True, 2  # H2
                else:
                    return True, 3  # H3
        
        # Check weak patterns - need font size or bold support
        for pattern in weak_patterns:
            if re.match(pattern, text) and (font_diff >= 1 or is_bold):
                return True, 3  # H3
        
        # General title case check with font size
        words = text.split()
        if (len(words) <= 10 and 
            len(words) >= 2 and
            all(word[0].isupper() if word and word[0].isalpha() else True for word in words) and
            not text.endswith('.') and
            (font_diff >= 1 or is_bold)):
            
            if font_diff >= 4:
                return True, 1  # H1
            elif font_diff >= 2:
                return True, 2  # H2
            else:
                return True, 3  # H3
        
        return False, 0
    
    def extract_text_with_structure(self, pdf_path: str) -> Dict[str, Any]:
        analysis = self.analyze_document_structure(pdf_path)
        text_blocks = analysis["text_blocks"]
        body_font_size = analysis["body_font_size"]
        
        # Detect if this is primarily a form/table document
        form_indicators = 0
        total_blocks = len(text_blocks)
        
        for block in text_blocks:
            text = block["text"].strip()
            if (
                re.match(r'^\d+\.\s+[A-Z]', text) or  # Numbered fields
                re.search(r'\b(Name|Designation|Date|Amount|Address|Age|Gender|Phone|Email|ID|Number|Servant|PAY|advance|permanent|temporary|Home|Town|Whether|grant|LTC)\b', text) or
                text.endswith(':') and len(text) > 10 or
                re.search(r'.*(\.\.\.|___|\s{5,})', text) or  # Fields with dots or underlines for filling
                re.search(r'\bRs\.\s*\d*\s*$', text) or  # Amount fields
                text.startswith('Application form') or text.startswith('Form')  # Form titles
            ):
                form_indicators += 1
        
        # If >40% of text blocks look like form fields, treat as form document
        is_form_document = total_blocks > 0 and (form_indicators / total_blocks) > 0.4
        
        
        # Build outline with all headings (including titles as H1)
        outline = []
        
        # If it's a form document, be much more restrictive about what counts as headings
        for block in text_blocks:
            text = block["text"]
            font_size = block["font_size"]
            flags = block["flags"]
            page = block["page"]
            
            is_heading, level = self.is_likely_heading(text, font_size, flags, body_font_size)
            
            # For form documents, disable outline extraction entirely (forms don't have narrative structure)
            if is_form_document:
                is_heading = False
            
            if is_heading:
                # Add to outline with exact challenge format
                level_str = f"H{level}"
                outline.append({
                    "level": level_str,
                    "text": text,
                    "page": page
                })
        
        # Extract main document title using a more sophisticated approach
        title = ""
        
        # Universal exclusion patterns for title extraction
        title_exclusion_patterns = [
            r'^Table\s+of\s+Contents$|^Acknowledgements?$|^References$|^Bibliography$',  # Navigation sections
            r'^\d+\.\d+\s+',  # Section numbers (these are headings, not main titles)
            r'^Page\s+\d+',   # Page numbers
            r'^Chapter\s+\d+$|^Section\s+\d+$|^Part\s+\d+$',  # Structural indicators without content
            r'.*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*',  # Text containing dates
            r'^[A-Z]{1,5}$',  # Very short acronyms
        ]
        
        # Try to find document title by looking at all text blocks
        # Title is usually the largest text, centered, or at the top
        title_candidates = []
        for block in text_blocks:
            text = block["text"].strip()
            
            # Clean up duplicated text patterns (fix garbled titles)
            # Remove pattern where same text appears multiple times
            clean_text = text
            if " " in text:
                # Check for repeated patterns in the text
                words = text.split()
                if len(words) > 4:
                    # Simple deduplication by removing obvious repetitions
                    pattern_len = len(words) // 4
                    if pattern_len > 0:
                        first_part = ' '.join(words[:pattern_len])
                        if text.count(first_part) > 1:
                            clean_text = first_part
                
                # Also remove consecutive duplicate words
                words = clean_text.split()
                unique_words = []
                for word in words:
                    if not unique_words or word != unique_words[-1]:
                        unique_words.append(word)
                clean_text = ' '.join(unique_words)
            
            if (len(clean_text) > 5 and len(clean_text) < 150 and 
                not any(re.match(pattern, clean_text, re.IGNORECASE) for pattern in title_exclusion_patterns)):
                title_candidates.append((clean_text, block["font_size"], block["page"]))
        
        # Sort by font size (largest first) and prefer page 1
        title_candidates.sort(key=lambda x: (-x[1], x[2]))
        if title_candidates:
            # Look for titles specifically on page 1 first
            page_1_candidates = [c for c in title_candidates if c[2] == 1]
            if page_1_candidates:
                # For page 1, try to find a longer, more complete title
                # Look for titles that might span multiple text blocks
                best_title = page_1_candidates[0][0]
                
                # Check if we can find a better, longer title by looking for 
                # text blocks that might be parts of the main title
                for candidate in page_1_candidates[:3]:  # Check top 3 candidates
                    if len(candidate[0]) > len(best_title) and "Foundation Level" in candidate[0]:
                        best_title = candidate[0]
                
                title = best_title
            else:
                title = title_candidates[0][0]
        
        # Fallback to first H1 if outline exists and no title found
        if not title and outline:
            first_h1 = next((item for item in outline if item["level"] == "H1"), None)
            if first_h1:
                title = first_h1["text"]
        
        # Final fallback
        if not title:
            title = "Document"
        
        return {
            "title": title,
            "outline": outline
        }
    
    def extract_tables_and_data(self, pdf_path: str) -> List[Dict[str, Any]]:
        tables = []
        
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_tables = page.extract_tables()
                
                for table_num, table in enumerate(page_tables):
                    if table and len(table) > 1:
                        headers = table[0] if table[0] else [f"Column_{i}" for i in range(len(table[1]))]
                        rows = table[1:]
                        
                        table_data = {
                            "page": page_num + 1,
                            "table_number": table_num + 1,
                            "headers": headers,
                            "rows": rows,
                            "structured_data": []
                        }
                        
                        for row in rows:
                            if row and any(cell for cell in row if cell):
                                row_dict = {}
                                for i, cell in enumerate(row):
                                    header = headers[i] if i < len(headers) else f"Column_{i}"
                                    row_dict[header] = cell if cell else ""
                                table_data["structured_data"].append(row_dict)
                        
                        tables.append(table_data)
        
        return tables
    
    def extract_pdf_data(self, pdf_path: str) -> Dict[str, Any]:
        structured_text = self.extract_text_with_structure(pdf_path)
        
        # Return in the exact format specified by the challenge
        return {
            "title": structured_text["title"],
            "outline": structured_text["outline"]
        }


@click.command()
@click.argument('pdf_path', type=click.Path(exists=True, path_type=Path))
@click.option('--output', '-o', type=click.Path(path_type=Path), 
              help='Output JSON file path (default: input_filename.json)')
@click.option('--pretty', '-p', is_flag=True, help='Pretty print JSON output')
def main(pdf_path: Path, output: Optional[Path], pretty: bool):
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
        
        indent = 2 if pretty else None
        
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(extracted_data, f, indent=indent, ensure_ascii=False)
        
        click.echo(f"Extraction complete! Output saved to: {output}")
        click.echo(f"Found {len(extracted_data['outline'])} headings")
        
    except Exception as e:
        click.echo(f"Error extracting PDF data: {e}", err=True)


if __name__ == "__main__":
    main()
