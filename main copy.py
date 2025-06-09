from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
from PIL import Image
import io
import base64
from openai import AzureOpenAI
import uvicorn


app = FastAPI()

# Configure your GPT-4o API key here
client = AzureOpenAI(
    api_key="7luBW95hdzPGbjLW1ib2bJMpKOA5D4GeVK14Ms1VjAd25Yv14Fe6JQQJ99BDAC4f1cMXJ3w3AAAAACOGqjaP",  # Replace with your actual API key
    api_version="2025-01-01-preview", # Replace with your actual API version
    azure_endpoint="https://at-aiagent-ai-studio-wu.cognitiveservices.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2025-01-01-preview", # Replace with your actual Azure endpoint
)

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
    return buffered.getvalue()

def pdf_to_images(pdf_bytes):
    images = []
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page_index in range(len(pdf)):
        page = pdf[page_index]
        pix = page.get_pixmap(dpi=200)
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
                                "Extract the following fields from the invoice image and provide ONLY a JSON object in this exact format:\n"
                                "{\n"
                                "  \"title\": string or null (sometimes it is the name of the vendor avialble as logo),\n"
                                "  \"invoice_id\": string or null (sometimes it is available as 'INVOICE #' or 'Invoice Number'),\n"
                                "  \"purchase_order\": string or null,\n"
                                "  \"amount\": string or null (sometimes available as 'Invoice Total' or 'Amount Due'),\n"
                                "  \"invoice_date\": string or null (it is available as 'Invoice Date')\n"
                                "}\n\n"
                                "If the invoice image contains information from several invoices,read each page carefully and create an array of the above JSON objects for each invoice."
                                "Return ONLY the JSON with no markdown formatting, explanations, or additional text."
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

@app.post("/process-pdf")
async def process_pdf(file: UploadFile = File(...)):
    print("inside function")
    if not file.filename.endswith(".pdf"):
       raise HTTPException(status_code=400, detail="File must be a PDF.")
    
    pdf_bytes = await file.read()    
    try:
        images = pdf_to_images(pdf_bytes)
        results = []
        
        #new_img = join_images_from_bytes(images)
        #response = call_gpt4o_with_image(new_img)
        #results.append(response)
     
        for img in images:
            response = call_gpt4o_with_image(img)
            results.append(response)
        results = [call_gpt4o_with_image(img) for img in images]
        #return JSONResponse(content={"results": results})
        return  results 
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == '__main__':
    uvicorn.run('main:app', host='0.0.0.0', port=8000)
#https://chatgpt.com/c/681a1eee-5c24-800e-bb11-ddfeeb6f79d8