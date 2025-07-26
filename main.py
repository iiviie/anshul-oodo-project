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
        
        # Check formatting flags
        is_bold = bool(flags & 2**4)  # Bold flag
        
        # Font size thresholds
        font_diff = font_size - body_font_size
        
        # Strong heading indicators
        strong_patterns = [
            r'^[A-Z][A-Z\s]{3,}$',  # ALL CAPS headings
            r'^\d+\.\s+[A-Z]',      # Numbered headings like "1. Introduction"
            r'^Chapter\s+\d+',      # Chapter headings
            r'^Section\s+\d+',      # Section headings
            r'^Round\s+\d+[A-Z]?:',  # Round headings like "Round 1A:"
            r'^Part\s+\d+',         # Part headings
            r'^[IVX]+\.\s+[A-Z]',   # Roman numeral headings
            r'^Welcome\s+to\s+',    # Welcome phrases (often titles)
            r'^"[^"]+"\s*Challenge', # Challenge titles with quotes
        ]
        
        # Medium heading indicators
        medium_patterns = [
            r'^[A-Z][a-z]+(\s+[A-Z][a-z]+)*$',  # Title Case headings
            r'^[A-Z][a-z]+\s+(Requirements?|Criteria|Tips?|Notes?)$',  # Common section types
            r'^(What|How|Why|When|Where)\s+[A-Z]',  # Question-style headings
            r'^[A-Z][^.]{5,30}$',   # Short capitalized phrases
            r'^"[^"]*"$',           # Quoted titles
            r'.*"[^"]*".*Challenge.*', # Titles with quoted parts and "Challenge"
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
                if font_diff >= 3:
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
        
        # Build outline with all headings (including titles as H1)
        outline = []
        
        for block in text_blocks:
            text = block["text"]
            font_size = block["font_size"]
            flags = block["flags"]
            page = block["page"]
            
            is_heading, level = self.is_likely_heading(text, font_size, flags, body_font_size)
            
            if is_heading:
                # Add to outline with exact challenge format
                level_str = f"H{level}"
                outline.append({
                    "level": level_str,
                    "text": text,
                    "page": page
                })
        
        # Extract main document title from the first H1 or use a fallback
        title = "Document"
        if outline:
            first_h1 = next((item for item in outline if item["level"] == "H1"), None)
            if first_h1:
                title = first_h1["text"]
                # Don't remove it from outline - keep all headings
        
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
