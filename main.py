from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
from PIL import Image
import io
import base64
from openai import AzureOpenAI
import uvicorn
from email import policy
from email.parser import BytesParser
from pdf2image import convert_from_path,convert_from_bytes
import os

# Path to the .eml file
eml_file_path = "Invoice.eml"

# Output directory for extracted PDFs
output_dir = "extracted_pdfs"

app = FastAPI()

# Configure your GPT-4o API key here
client = AzureOpenAI(
    api_key="7luBW95hdzPGbjLW1ib2bJMpKOA5D4GeVK14Ms1VjAd25Yv14Fe6JQQJ99BDAC4f1cMXJ3w3AAAAACOGqjaP",  # Replace with your actual API key
    api_version="2025-01-01-preview", # Replace with your actual API version
    azure_endpoint="https://at-aiagent-ai-studio-wu.cognitiveservices.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview", # Replace with your actual Azure endpoint
)
# === Read PDF Text ===
def extract_text_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text()
    return full_text

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    # Open PDF from bytes
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""

    for page in doc:
        full_text += page.get_text()

    return full_text

# === Your Prompt ===
INVOICE_EXTRACTION_PROMPT = """
            You are an intelligent document parser tasked with extracting structured data from scanned or digital invoice documents. The document may contain one or multiple invoices, often across several pages.

            From each invoice, identify and extract the following fields in a consistent JSON array format. Maintain the order of invoices as found in the document.
            Extract and return data in this exact JSON structure:

            [{Invoice Number: <Invoice number string>,Invoice Date: <Date in MM-DD-YYYY format>,Vendor Name: <Vendor or company name (from header or logo)>,Purchase Order: <Purchase order number or code>,Total Amount: <Total invoice amount as number, no currency symbol>,}]

            ### Guidelines:
            - Always return an array of invoice objects, even if only one invoice is found.
            - Vendor Name may be found in headers, footers, or logos (e.g., 'Ingram Micro Inc.', 'Park Place Technologies LLC').
            - Purchase Order may appear as PO, Customer PO, or embedded in descriptions—extract the most relevant code associated with order tracking.
            - Total Amount must include all charges (subtotal + tax + freight) if listed, or the final total if directly available.
            - Parse all pages and ensure no invoice is missed, especially in documents with multiple pages or summary sections.

            Return only the JSON. Do not include explanations, notes, or any other commentary.
            """

def join_images_from_bytes(image_bytes_list):
    # Load images from bytes
    images = [Image.open(io.BytesIO(img_bytes)) for img_bytes in image_bytes_list]

    # Convert all images to the same mode and size if needed (optional)
    # images = [img.convert('RGB') for img in images]
    max_width = max(img.width for img in images)
    total_height = sum(img.height for img in images)
    new_img = Image.new('RGB', (max_width, total_height))
    y_offset = 0
    for img in images:
        new_img.paste(img, (0, y_offset))
        y_offset += img.height
    buffered = io.BytesIO()
    new_img.save(buffered, format="PNG")
    new_img.save("combined_pages.png", "PNG")
    return buffered.getvalue()

def pdf_to_images(pdf_bytes):
    images = []
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page_index in range(len(pdf)):
        page = pdf[page_index]
        pix = page.get_pixmap(dpi=400)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        images.append(buffered.getvalue())
    return images

def call_gpt4o_with_image(image_bytes):
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a specialized invoice data extraction assistant. Extract the requested fields from the invoice image and return ONLY a valid JSON object with no additional text or explanations."
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "You are an intelligent document parser tasked with extracting structured data from scanned or digital invoice documents. The document may contain one or multiple invoices, often across several pages."

                                "From each invoice, identify and extract the following fields in a consistent JSON array format. Maintain the order of invoices as found in the document."
                                "Extract and return data in this exact JSON structure:"

                                "[{Invoice Number: <Invoice number string>,Invoice Date: <Date in YYYY-MM-DD format>,Vendor Name: <Vendor or company name (from header or logo)>,Purchase Order: <Purchase order number or code>,Total Amount: <Total invoice amount as number, no currency symbol>,}]"

                                "### Guidelines:"
                                "- Always return an array of invoice objects, even if only one invoice is found."
                                "- Vendor Name may be found in headers, footers, or logos (e.g., 'Ingram Micro Inc.', 'Park Place Technologies LLC')."
                                "- Purchase Order may appear as PO, Customer PO, or embedded in descriptions—extract the most relevant code associated with order tracking."
                                "- Total Amount must include all charges (subtotal + tax + freight) if listed, or the final total if directly available."
                                "- Parse all pages and ensure no invoice is missed, especially in documents with multiple pages or summary sections."

                                "Return only the JSON. Do not include explanations, notes, or any other commentary."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                            "detail": "high",
                        },
                    ],
                }
            ],
            max_tokens=1000,
            temperature=0,  
            response_format={"type": "json_object"} 
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error communicating with OpenAI: {str(e)}"


def send_to_gpt4o_azure(prompt, pdf_text):

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a specialized invoice data extraction assistant. Extract the requested fields from the invoice image and return ONLY a valid JSON object with no additional text or explanations."
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (prompt),
                        },
                        {
                            "type": "text",                            
                            "text": pdf_text,
                        },
                    ],
                }
            ],
            max_tokens=1000,
            temperature=0,  
            response_format={"type": "json_object"} 
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error communicating with OpenAI: {str(e)}"



@app.post("/process-pdf")
async def process_pdf(file: UploadFile = File(...)):
    print("inside function psot")
    if not file.filename.lower().endswith(".pdf"):
       raise HTTPException(status_code=400, detail="File must be a PDF.")
    
    pdf_bytes = await file.read()    
    try:
        #images = pdf_to_images(pdf_bytes)
        results = []
        
        #new_img = join_images_from_bytes(images)
        #response = call_gpt4o_with_image(new_img)
        #results.append(response)
     
        #for img in images:
        #    response = call_gpt4o_with_image(img)
        #    results.append(response)
        #results = [call_gpt4o_with_image(img) for img in images]
        #return JSONResponse(content={"results": results})
        pdf_path = 'ingrammicrous_5 page inv TEST.pdf'
        pdf_text = extract_text_from_pdf_bytes(pdf_bytes) #extract_text_from_pdf(pdf_path)
        response = send_to_gpt4o_azure(INVOICE_EXTRACTION_PROMPT, pdf_text)
        return  response
        #return  results 
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/extract-attachments")
async def extract_attachments(eml_file: UploadFile = File(...)):

    print("Inside extract-attachments")
    content = await eml_file.read()
    msg = BytesParser(policy=policy.default).parsebytes(content)
    pdf_files = []
    print(msg)
    # Go through each part of the email
    for part in msg.iter_attachments():
        content_type = part.get_content_type()
        filename = part.get_filename()

        if filename and filename.lower().endswith(".pdf"):
            pdf_bytes = part.get_payload(decode=True)
            encoded_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
            pdf_files.append({
                "filename": filename,
                "content_base64": encoded_pdf
            })
            #filepath = os.path.join(filename)
            #with open(filepath, 'wb') as pdf_file:
            #    pdf_file.write(part.get_payload(decode=True))
            #print(f"✅ Extracted PDF: {filepath}")
            return JSONResponse(content={"pdf_files": pdf_files})

    print("❌ No PDF attachment found in the .eml file.")
    return None


if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0', port=8000)
#https://chatgpt.com/c/681a1eee-5c24-800e-bb11-ddfeeb6f79d8
#https://chatgpt.com/c/6846eba1-6688-800e-a3ed-6eaaa138a52d