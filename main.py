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
import json
import re
from collections import defaultdict
from pathlib import Path

json_obj1 = {
    "Invoice Number": "1183022",
    "Invoice Date": "10-01-2023",
    "Vendor Name": "Emburse Inc.",
    "Purchase Order": "A60",
    "Total Amount": 634.10
}

json_obj2= {
        'Invoice Number' :'RAZ202402002',
        'Invoice Date' :'2024-02-29',
        'Vendor Name' :'Atidan Technologies Pvt Ltd',
        'Purchase Order':'',
        'Total Amount' :68576
}

json_arr = {
  "invoices": [
    {
      "Invoice Number": "30-21401-11",
      "Invoice Date": "05-08-2025",
      "Vendor Name": "Ingram Micro Inc.",
      "Purchase Order": "25MIA7536",
      "Total Amount": 314.26
    }
    ]
}

json_arr1 = {
  "Invoices": [
    {
      "Invoice Number": "30-21401-11",
      "Invoice Date": "05-08-2025",
      "Vendor Name": "Ingram Micro Inc.",
      "Purchase Order": "25MIA7536",
      "Total Amount": 314.26
    }
    ]
}
app = FastAPI()

# Configure your GPT-4o API key here
client = AzureOpenAI(
    #api_key="7luBW95hdzPGbjLW1ib2bJMpKOA5D4GeVK14Ms1VjAd25Yv14Fe6JQQJ99BDAC4f1cMXJ3w3AAAAACOGqjaP",  # Replace with your actual API key
    api_key="4vFtdPo0ily6CRvdsT9fI0FK5P6sTWB10yM3RTm9LDRYexIwppsXJQQJ99BFACYeBjFXJ3w3AAABACOGHbg1",
    api_version="2025-01-01-preview", # Replace with your actual API version
    #azure_endpoint="https://at-aiagent-ai-studio-wu.cognitiveservices.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview", # Replace with your actual Azure endpoint
    azure_endpoint="https://rz-vinvauto-openai.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview"
)

# === extract JSON schema ===
def extract_schema(obj):
    if isinstance(obj, dict):
        return {k: extract_schema(v) for k, v in obj.items()}
    elif isinstance(obj, list) and obj:
        return [extract_schema(obj[0])]  # assume list items have same schema
    else:
        return type(obj).__name__
    
# === compare JSON schema ==
def compare_schemas(json1, json2):
    return extract_schema(json1) == extract_schema(json2)

# === Read PDF Text ===
def extract_text_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text()
    return full_text

def get_page_count_from_pdf_bytes(pdf_bytes):
    pdf_file = fitz.open(stream=pdf_bytes, filetype="pdf")
    return len(pdf_file)

def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    # Open PDF from bytes
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""

    for page in doc:
        full_text += page.get_text()

    return full_text

def extract_invoice_number(text):
    """Extract invoice number using regex."""
    match = re.search(r'(?:INVOICE\s*#|Invoice\s*#:)\s*([\w\-]+)', text, re.IGNORECASE)
    return match.group(1) if match else None


def split_pdf_by_invoice_number(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    invoice_last_pages = {}
    files_bytes = []
    current_invoice = None
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()

        # Check for keywords
        if "invoice total" in text.lower() and "invoice" in text.lower():
            invoice_num = extract_invoice_number(text)
            if invoice_num:
                invoice_last_pages[invoice_num] = page_num  # New invoice found

    for invoice_num, page_num  in invoice_last_pages.items():
        new_doc = fitz.open()
        new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)        
        pdf_bytes_io = io.BytesIO()
        new_doc.save(pdf_bytes_io)
        new_doc.close()
        files_bytes.append(pdf_bytes_io.getvalue())

    return files_bytes
        
    
# === Your Prompt ===
INVOICE_EXTRACTION_PROMPT = """
            You are an intelligent document parser tasked with extracting structured data from scanned or digital invoice documents or pdf text. The document may contain one or multiple invoices, often across several pages.

            From each invoice, identify and extract the following fields in a consistent JSON array format. Always return an array of invoice objects, even if only one invoice is found. Maintain the order of invoices as found in the document.
            Extract and return data in this exact JSON structure:

            [{Invoice Number: <Invoice number string>,Invoice Date: <Date in YYYY-MM-DD format>,Vendor Name: <Vendor name (from logo, if no logo then header)>,Purchase Order: <usually marked as Purchase Order or PO Number or PO# or PO>,Total Amount: <Total invoice amount as number, no currency symbol>},...]

            ### Guidelines:
            - Always return an array of invoice objects, even if only one invoice is found.
            - Vendor Name may be found in headers, footers, or logos (e.g., 'Ingram Micro Inc.', 'Park Place Technologies LLC').
            - Purchase Order may appear as 'PO', 'CUSTOMER PO', 'Purchase Order' or 'PO Number' or 'PO#' or 'P.O. NUMBER' with separate heading. Send '' if not found any relavent value.
            - Total Amount must include all charges (subtotal + tax + freight) if listed, or the final total if directly available.
            - Parse all pages and ensure no invoice is missed, especially in documents with multiple pages or summary sections.
            - Invoice Date sometime marked as "Created Date"
            Return only the JSON. Do not include explanations, notes, or any other commentary.

            If the file is not recognized as valid invoice rather it is of Statement, Purchase order, Certificate, Notice, etc; then return the value 'No Invoice'
            Some vendors like Mimecast(Mimecast North America, Inc.) or Kaseya sometimes sends Invoice with heading Consolidated Invoice, so consider it as Invoice Only.
            For vendor 'Park Place Technologies LLC' the invoice has the heading as Credit Memo. Also, 'Invoice Number' marked as 'Credit', 'Invoice Date' is marked as 'Date','Vendor Name' is marked with value 'Park Place Technologies LLC', 'Purchase Order' as 'Purchase Order','Total Amount' is marked as 'Total'.
            For 'Lora M Cox' invoice file consider Venfor Name as 'Lora M Cox' not 'Razor Technology'. 
            For vendor 'Quantum', consider 'Statement of Account' as invoice.
            For vendor 'VOITH', consider 'Payment advice notification' as invoice.
            For vendor 'GRSM50 GORDON REES SCULLY MANSUKHANI', consider BILLING SUMMARY as invoice.
            For file contains the text 'NSM Insurance Group' then consider Vendor Name as 'NSM Insurance Group'
            For files contains 'REMIT TO: BDO' then consider Vendor Name as 'BDO Digital'
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
    #new_img.save("combined_pages.png", "PNG")
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

def call_gpt4o_with_image(prompt,image_bytes):
    print("Inside call_gpt4o_with_image: Start")
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
                            "text": (prompt),
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
        print("Inside call_gpt4o_with_image: End")
        return response.choices[0].message.content
    except Exception as e:
        return f"Error communicating with OpenAI: {str(e)}"


def call_gpt4o_with_text(prompt, pdf_text):

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a specialized invoice data extraction assistant. Extract the requested fields from the invoice text and return ONLY a valid JSON object with no additional text or explanations."
                },
                {
                    "role": "user",
                    "content": prompt + "\n\n" + pdf_text
                    # [
                    #     {
                    #         "type": "text",
                    #         "text": (prompt),
                    #     },
                    #     {
                    #         "type": "text",                            
                    #         "text": pdf_text,
                    #     },
                    # ]
                    
                    ,
                }
            ],
            max_tokens=10000,
            temperature=0,  
            response_format={"type": "json_object"} 
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error communicating with OpenAI: {str(e)}"



@app.post("/process-pdf")
async def process_pdf(file: UploadFile = File(...)):
    print("inside function process_pdf")
    if not file.filename.lower().endswith(".pdf"):
       raise HTTPException(status_code=400, detail="File must be a PDF.")
    
    pdf_bytes = await file.read()    
    try:
        response = ""
        invs=[]
        if(file.filename.lower().startswith("ingram")):
            pdfs = split_pdf_by_invoice_number(pdf_bytes)
            for bytes in pdfs:
                # images = pdf_to_images(bytes)         
                # new_img = join_images_from_bytes(images)
                # inv = call_gpt4o_with_image(INVOICE_EXTRACTION_PROMPT, new_img)

                pdf_text = extract_text_from_pdf_bytes(bytes) #extract_text_from_pdf(pdf_path)
                inv = call_gpt4o_with_text(INVOICE_EXTRACTION_PROMPT, pdf_text)

                invs.append(json.loads(inv))            
            json_object = {"invoices":invs}

        elif(get_page_count_from_pdf_bytes(pdf_bytes) > 4):
            pdf_text = extract_text_from_pdf_bytes(pdf_bytes) #extract_text_from_pdf(pdf_path)
            inv = call_gpt4o_with_text(INVOICE_EXTRACTION_PROMPT, pdf_text)
            
        else:
            images = pdf_to_images(pdf_bytes)         
            new_img = join_images_from_bytes(images)
            inv = call_gpt4o_with_image(INVOICE_EXTRACTION_PROMPT, new_img)
            # === All the response should be in same JSON format as per variable json_arr, if not then make it ===
            json_object = json.loads(inv)
            
        #To convert the key "Invoices" to lowercase ("invoices") in the given JSON
        if(compare_schemas(json_arr,json.loads(inv)) or compare_schemas(json_arr1,json.loads(inv))):
            json_object=json.loads(inv)
            json_object={k.lower(): v for k, v in json_object.items()}

        if(compare_schemas(json_obj1,json_object)):
            response = {"invoices": [json_object]}
        elif(compare_schemas(json_obj2,json_object)):
            response = {"invoices": [json_object]}
        elif(compare_schemas(json_arr,json_object)):
            response = json_object
        elif (json.dumps(json_object).find("No Invoice") != -1):
            response = '{"invoices":[{"Invoice Number":"NoInvoice","Invoice Date":"NoInvoice","Vendor Name":"NoInvoice","Purchase Order":"NoInvoice","Total Amount":0}]}'
        return  response

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
            return JSONResponse(content={"pdf_files": pdf_files})

    print("❌ No PDF attachment found in the .eml file.")
    return None


if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0', port=8000)
#https://chatgpt.com/c/681a1eee-5c24-800e-bb11-ddfeeb6f79d8
#https://chatgpt.com/c/6846eba1-6688-800e-a3ed-6eaaa138a52d