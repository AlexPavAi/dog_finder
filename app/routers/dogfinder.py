import hashlib
import uuid
from app.models.dog_document import DogType
from app.services.auth import VerifyToken
from typing import Any, List, Optional
from app.MyLogger import logger
from app.models.api_response import APIResponse
from fastapi import APIRouter, File, Form, Security, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app.helpers.model_helper import create_embedding_model
from app.helpers.helper import detect_image_mimetype, resize_image_and_convert_to_format, timeit
from app.helpers.weaviate_helper import FilterValueTypes
from app.models import DogDocument
from weaviate.util import generate_uuid5
from app.models.predicates import Predicate, Filter, and_, or_
# from sentence_transformers import SentenceTransformer
import os
from datetime import datetime, timedelta
from PIL import Image
from io import BytesIO
import base64
from app.services.ivectordb_client import IVectorDBClient
from app.services.weaviate_vectordb_client import WeaviateVectorDBClient

logger.info("Starting up the dogfinder router")

router = APIRouter(prefix="/dogfinder")
vecotrDBClient: IVectorDBClient

IS_MATCHED_FIELD = "isMatched"
UUID_FIELD = "uuid5"
DOG_ID_FIELD = "dogId"

# CHANGE THIS TO FALSE ON PRODUCTION
IS_VERIFIED_FIELD_DEFAULT_VALUE = False

dog_class_definition = {
        "class": "Dog",
        "invertedIndexConfig": {
            "indexNullState": True,
            "indexTimestamps": True
        },
        "properties": [
            {
                "name": "breed",
                "dataType": ["text"],
                "description": "Name of dog breed"
            },
            {
                "name": "size",
                "dataType": ["text"],
                "description": "The dog's size"
            },
            {
                "name": "color",
                "dataType": ["text"],
                "description": "The dog's color"
            },
            {
                "name": "extraDetails",
                "dataType": ["text"],
                "description": "Any extra detail that can be returned to the user to identify the dog"
            },
            {
                "name": "type",
                "dataType": ["text"],
                "description": "Found Or Lost"
            },
            {
                "name": "filename",
                "dataType": ["text"],
                "description": "Uploaded filename"
            },
            {
                "name": "imageBase64",
                "dataType": ["blob"],
                "description": "Image in base64 format"
            },
            {
                "name": IS_MATCHED_FIELD,
                "dataType": ["boolean"],
                "description": "was the dog found?"
            },
            {
                "name": DOG_ID_FIELD,
                "dataType": ["text"],
                "description": "id of the dog"
            },
            {
                "name": "contactName",
                "dataType": ["text"],
                "description": "Contact name"
            },
            {
                "name": "contactPhone",
                "dataType": ["text"],
                "description": "Contact phone"
            },
            {
                "name": "contactEmail",
                "dataType": ["text"],
                "description": "Contact email"
            },
            {
                "name": "contactAddress",
                "dataType": ["text"],
                "description": "Contact address"
            },
            {
                "name": "isVerified",
                "dataType": ["boolean"],
                "description": "is the dog entry verified?"
            },
            {
                "name": "imageContentType",
                "dataType": ["text"],
                "description": "the image content type"
            },
            {
                "name": "chipNumber",
                "dataType": ["text"],
                "description": "the chip number of the dog if exists"
            },
            {
                "name": "location",
                "dataType": ["text"],
                "description": "the location of the dog"
            }
        ]
    }

def hash_image(image):
    return hashlib.md5(image.encode()).hexdigest()

def generate_dog_id():
    return str(uuid.uuid4())

# This is the request model for the add_documents endpoint
class DocumentRequest(BaseModel):
    documents: List[DogDocument]

# This is the request model for the query endpoint
RETURN_PROPERTIES = ["type", "breed", "size", "color", "extraDetails", "filename", "imageBase64", IS_MATCHED_FIELD, DOG_ID_FIELD, "contactName", "contactPhone", "contactEmail", "contactAddress", "isVerified", "imageContentType","chipNumber","location"]

class QueryRequest(BaseModel):
    type: DogType
    imageBase64: str
    breed: Optional[str] = None
    top: int = 10
    return_properties: Optional[List[str]] = RETURN_PROPERTIES
    isVerified: Optional[bool] = True

class DogFoundRequest(BaseModel):
    dogId: str

@router.on_event("startup")
async def startup_event():
    """
    Load all the necessary models and data once the server starts.
    """
    global vecotrDBClient

    # Create the vector db client, connecting to the weaviate instance
    vecotrDBClient = WeaviateVectorDBClient(url=f"{os.getenv('WEAVIATE_HOST', 'http://localhost:8080')}")

    # Create the schema
    vecotrDBClient.create_schema(class_name="Dog", class_obj=dog_class_definition)


auth = VerifyToken()

@router.get("/private")
def private(auth_result: str = Security(auth.verify)):
    return auth_result

@router.get("/private-scoped")
def private_scoped(auth_result: str = Security(auth.verify, scopes=['read:dogs'])):
    return auth_result

@router.post("/query/", response_model=APIResponse)
async def query(type: DogType = Form(...), breed: Optional[str] = Form(None), img: UploadFile = File(...), top: int = Form(10), isVerified: Optional[bool] = Form(True)):
    try:
        # Handle the image, resize it and convert it to base64 with webp format and get the content type
        img_base64, img_content_type = handle_uploaded_image(img)

        # Create QueryRequest
        queryRequest = QueryRequest(type=type, breed=breed, imageBase64=img_base64, top=top, isVerified=isVerified)

        # Query the database
        results = query_vector_db(queryRequest, query)

        api_response = APIResponse(status_code=200, message=f"Queried {len(results)} results from the vecotrdb", data={ "total": len(results), "results": results })
    except Exception as e:
        logger.error(f"Error while querying the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the vecotrdb: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.post("/search_found_dogs/", response_model=APIResponse)
async def search_found_dogs(breed: Optional[str] = Form(None), img: UploadFile = File(...), top: int = Form(10)):
    try:
        # Handle the image, resize it and convert it to base64 with webp format and get the content type
        img_base64, img_content_type = handle_uploaded_image(img)

        # Create QueryRequest
        queryRequest = QueryRequest(type=DogType.FOUND, breed=breed, imageBase64=img_base64, top=top, isVerified=True)

        # Query the database
        results = query_vector_db(queryRequest, query)

        api_response = APIResponse(status_code=200, message=f"Queried {len(results)} results from the vecotrdb", data={ "total": len(results), "results": results })
    except Exception as e:
        logger.error(f"Error while querying the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the vecotrdb: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.post("/search_lost_dogs/", response_model=APIResponse)
async def search_lost_dogs(breed: Optional[str] = Form(None), img: UploadFile = File(...), top: int = Form(10)):
    try:
        # Handle the image, resize it and convert it to base64 with webp format and get the content type
        img_base64, img_content_type = handle_uploaded_image(img)

        # Create QueryRequest
        queryRequest = QueryRequest(type=DogType.LOST, breed=breed, imageBase64=img_base64, top=top, isVerified=True)

        # Query the database
        results = query_vector_db(queryRequest, query)

        api_response = APIResponse(status_code=200, message=f"Queried {len(results)} results from the vecotrdb", data={ "total": len(results), "results": results })
    except Exception as e:
        logger.error(f"Error while querying the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the vecotrdb: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.get("/get_unverified_documents/", response_model=APIResponse)
async def get_unverified_documents():
    try:
        # Query the database
        results = vecotrDBClient.query(class_name="Dog", query=None, query_embedding=None, limit=10000, offset=None, filter=and_(*[Predicate(["isVerified"], "Equal", False, FilterValueTypes.valueBoolean)]).to_dict(), properties=RETURN_PROPERTIES)

        api_response = APIResponse(status_code=200, message=f"Queried {len(results)} results from the vecotrdb", data={ "total": len(results), "results": results })
    except Exception as e:
        logger.error(f"Error while querying the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the vecotrdb: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)
    
# Endpoint for quering the database without the need for a query image, only DOG_ID_FIELD
@router.get("/query_by_dog_id/", response_model=APIResponse)
async def query_by_dog_id(dogId: str):
    try:
        # Query the database
        results = vecotrDBClient.query(class_name="Dog", query=None, query_embedding=None, limit=1, offset=None, filter=and_(*[Predicate([DOG_ID_FIELD], "Equal", dogId, FilterValueTypes.valueText)]).to_dict(), properties=RETURN_PROPERTIES)

        api_response = APIResponse(status_code=200, message=f"Queried {len(results)} results from the vecotrdb", data={ "total": len(results), "results": results })
    except Exception as e:
        logger.error(f"Error while querying the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the vecotrdb: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)


def query_vector_db(queryRequest: QueryRequest, query: Optional[str] = None):
    # Create the embedding model
    logger.info(f"Creating embedding model")
    embedding_model, cache_info = create_embedding_model()

    # Open the image from the base64 string to PIL Image
    query_image = create_pil_image(queryRequest.imageBase64)

    # Embed the query image
    query_embedding = embed_query(query_image, embedding_model)

    # Build the filter with conditions to query the vector db
    filter = build_filter(queryRequest)

    # Query the database
    logger.info(f"Querying the database")
    results = vecotrDBClient.query(class_name="Dog", query=query, query_embedding=query_embedding, limit=queryRequest.top, offset=None, filter=filter.to_dict(), properties=queryRequest.return_properties)

    return results

@router.post("/add_document", response_model=APIResponse)
async def add_document(type: DogType = Form(...), 
                       img: UploadFile = File(...), 
                       breed: Optional[str] = Form(None), 
                       size: Optional[str] = Form(None), 
                       color: Optional[str] = Form(None), 
                       extraDetails: Optional[str] = Form(None), 
                       contactName: Optional[str] = Form(None), 
                       contactPhone: Optional[str] = Form(None), 
                       contactEmail: Optional[str] = Form(None), 
                       contactAddress: Optional[str] = Form(None),
                       chipNumber: Optional[str] = Form(None),
                       location: Optional[str] = Form(None)):
    # logger.info(f"Document Request: {documentRequest}")

    try:
        # Add the document to the database
        documents = []

        # Handle the image, resize it and convert it to base64 with webp format and get the content type
        img_base64, img_content_type = handle_uploaded_image(img)

        # Create a PIL Image from the base64 string
        document_image = create_pil_image(img_base64)

        # Create DogDocument
        dogDocument = DogDocument(type=type.value,
                                  imageBase64=img_base64, 
                                  filename=img.filename, 
                                  contactName=contactName, 
                                  contactPhone=contactPhone, 
                                  contactEmail=contactEmail, 
                                  contactAddress=contactAddress,
                                  isVerified=IS_VERIFIED_FIELD_DEFAULT_VALUE, 
                                  imageContentType=img_content_type, 
                                  breed=breed, 
                                  size=size, 
                                  color=color, 
                                  extraDetails=extraDetails,
                                  chipNumber=chipNumber,
                                  location=location
                                  )

        # Create the embedding model
        logger.info(f"Creating embedding model")
        embedding_model, cache_info = create_embedding_model()

        # Embed the document image
        dog_embedding = embed_query(document_image, embedding_model)

        try:
            # Create the data object
            data_properties = create_data_properties(dogDocument, generate_dog_id())
            # Create a uuid based on the filename
            data_properties[UUID_FIELD] = generate_uuid5({"breed": dogDocument.breed, "type": dogDocument.type,"imageHash":hash_image(dogDocument.imageBase64)})
            data_properties["document_embedding"] = dog_embedding

            documents.append(data_properties)
        except Exception as e:
            logger.error(f"Error while creating data_properties for document: {e}")

        # Add the documents to the database
        logger.info(f"Add documents batch")
        result = vecotrDBClient.add_documents_batch("Dog", documents)

        api_response = APIResponse(status_code=200, message=f"Added documents to the vecotrdb", data=result)
    except Exception as e:
        logger.error(f"Error while adding documents to the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while adding documents to the vecotrdb: {e}")
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.post("/add_documents", response_model=APIResponse)
async def add_documents(documentRequest: DocumentRequest):
    logger.info(f"Document Request: {documentRequest}")

    try:
        # Add the documents to the database
        documents_length = len(documentRequest.documents)
        logger.info(f"Adding {documents_length} documents")
        documents = []

        # Create the embedding model
        logger.info(f"Creating embedding model")
        embedding_model, cache_info = create_embedding_model()

        # Set every new document to not verified
        for document in documentRequest.documents:
            document.isVerified = IS_VERIFIED_FIELD_DEFAULT_VALUE
            document.imageBase64, document.imageContentType = resize_image_and_convert_to_format(document.imageBase64, (500, 500))

        # Embed the documents images
        embedding_results = embed_documents([create_pil_image(document.imageBase64) for document in documentRequest.documents], embedding_model)
        request_dog_id = generate_dog_id()

        # Iterate over the documents and add them to the database
        for i, document in enumerate(documentRequest.documents):
            try:
                # Create the data object
                data_properties = create_data_properties(document, request_dog_id)

                # Create a uuid based on the filename
                data_properties[UUID_FIELD] = generate_uuid5({"breed": document.breed, "type": document.type, "imageHash": hash_image(document.imageBase64)})

                # Embed the document
                logger.info(f"Setting Embedding document [{document.filename}] {i+1} of {documents_length}")
                data_properties["document_embedding"] = embedding_results[i]


                documents.append(data_properties)
            except Exception as e:
                logger.error(f"Error while creating data_properties for document [{document.filename}] {i+1} of {documents_length}: {e}")

        # Add the documents to the database
        logger.info(f"Add documents batch")
        result = vecotrDBClient.add_documents_batch("Dog", documents)

        api_response = APIResponse(status_code=200, message=f"Added documents to the vecotrdb", data=result)
    except Exception as e:
        logger.error(f"Error while adding documents to the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while adding documents to the vecotrdb: {e}")
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

# Add an endpoint to set isVerified to True
@router.post("/verify_document", response_model=APIResponse)
async def verify_document(dogId: str):
    logger.info(f"Verify document: {dogId}")

    try:
        # Add the documents to the database
        logger.info(f"Verify document")
        result = vecotrDBClient.update_document("Dog", dogId, {
            "isVerified": True,
        })

        api_response = APIResponse(status_code=200, message=f"Verified document in the vecotrdb", data=result)
    except Exception as e:
        logger.error(f"Error while verifying document in the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while verifying document in the vecotrdb: {e}")
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)


@router.get("/get_schema")
async def get_schema(class_name: str):
    return vecotrDBClient.get_schema(class_name)

@router.delete("/clean_all", response_model=APIResponse)
async def clean_all():
    logger.info("Deleting all documents from the vectordb")

    # Delete all objects from the database
    result = vecotrDBClient.clean_all("Dog", dog_class_definition)

    # If result.success is False return 500 with result.message
    if result.get("success") is False:
        api_response = APIResponse(status_code=500, message=f"Error deleting all documents from the vectordb: {result.get('message')}")
    else:
        api_response = APIResponse(status_code=200, message=f"All documents were deleted from the vectordb")

    # return back a json response and set the status code to api_response.status_code
    return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.post("/doc_found", response_model=APIResponse)
async def doc_found(foundRequest: DogFoundRequest):

    vecotrDBClient.update_document("Dog", foundRequest.dogId,{
                "isMatched": True,
            })

    api_response = APIResponse(status_code=200, message=f"matched dog marked", data={})
    # return back a json response and set the status code to api_response.status_code
    return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

# build a predicate for the properties, breed, type, if they are not None with and_ between them
@timeit
def build_filter(queryRequest: QueryRequest) -> Optional[Filter]:
    logger.info(f"Building the filter for queryRequest: {queryRequest}")

    # create a list of predicates
    predicates = []

    # add breed predicate
    if queryRequest.breed is not None:
        predicates.append(Predicate(["breed"], "Equal", queryRequest.breed, FilterValueTypes.valueText))

    # add type predicate
    if queryRequest.type is not None:
        predicates.append(Predicate(["type"], "Equal", queryRequest.type.value, FilterValueTypes.valueText))

    # add isVerified predicate
    # Commented out because we want to return verified and unverified dogs for now until we have a way to verify them
    # if queryRequest.isVerified is not None:
    #     predicates.append(Predicate(["isVerified"], "Equal", queryRequest.isVerified, FilterValueTypes.valueBoolean))

    # add isMatched predicate, we only want to return dogs that are not found
    predicates.append(Predicate([IS_MATCHED_FIELD], "Equal", False, FilterValueTypes.valueBoolean))

    # if there are predicates return and_ between them
    if (len(predicates) > 0):
        return and_(*predicates)

    # if there are no predicates return None
    return None

def create_data_properties(document: DogDocument, dog_id:str) -> dict[str, Any]:
    # Transform document to dictionary
    data_properties = document.model_dump()
    
    # Transform datetime objects to string
    for key, val in data_properties.items():
        if isinstance(val, datetime):
            data_properties[key.lower()] = val.strftime("%Y-%m-%dT%H:%M:%S.%SZ") if val else None

    data_properties[DOG_ID_FIELD] = dog_id

    return data_properties

def get_base64(img_content):
    img_base64 = base64.b64encode(img_content).decode('utf-8')
    logger.debug(f"Image base64: {img_base64}")

    return img_base64

def create_pil_image(img_base64):
    # Open the image from the base64 string to PIL Image
    query_image = Image.open(BytesIO(base64.b64decode(img_base64)))

    return query_image

@timeit
def embed_documents(documents, embedding_model):
    logger.info(f"Embedding documents {len(documents)} documents: '{documents}'")

    if (embedding_model is not None):
        texts_embedding = embedding_model.encode(documents)
        # log texts_embedding 2 dimensions lenght of the first element as well
        logger.info(f"Texts embedding: {texts_embedding} Dimensions: [{len(texts_embedding)},{len(texts_embedding[0])}]")
    else:
        # create a random embedding
        texts_embedding = [[0.1] * 512] * len(documents)

    return texts_embedding

@timeit
def embed_query(query, embedding_model):
    logger.info(f"Embedding query: '{query}'")

    if (embedding_model is not None):
        query_embedding = embedding_model.encode(query)
    else:
        # create a random embedding
        query_embedding = [0.1] * 512

    return query_embedding

def _json_serializable(value: Any) -> Any:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value

def handle_uploaded_image(img):
    """
    This function takes an uploaded image and returns a resized and converted image in base64 format and its content type.

    Args:
        img: An uploaded image.

    Returns:
        A tuple containing the resized and converted image in base64 format and its content type.
    """
    # Read the image content from the uploaded file
    img_content = img.file.read()

    # Convert the image content to base64 format
    img_base64 = get_base64(img_content)

    # Resize the image and convert it to the desired format
    img_base64, img_content_type = resize_image_and_convert_to_format(img_base64, (1024, 1024))

    # Return the resized and converted image and its content type
    return img_base64, img_content_type
