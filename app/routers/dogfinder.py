import hashlib
import uuid
from app.DTO.dog_dto import DogDTO, DogImageDTO, DogType
from app.helpers.image_helper import create_pil_images, get_base64
from app.services.auth import VerifyToken
from typing import Any, List, Optional
from app.MyLogger import logger
from app.services.dog_service import DogWithImagesService
from app.services.vectordb_indexer import VectorDBIndexer
from app.viewmodels.api_response import APIResponse
from app.viewmodels.dog_viewmodel import RETURN_PROPERTIES, DogFullDetailsResponse, DogImageResponse, DogAddRequest, DogMatchedRequest, DogResponse, DogSearchRequest
from fastapi import APIRouter, File, Form, Security, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app.helpers.model_helper import create_embedding_model, embed_query
from app.helpers.helper import detect_image_mimetype, generate_dog_id, hash_image, resize_image_and_convert_to_format, timeit
from app.helpers.weaviate_helper import FilterValueTypes
from weaviate.util import generate_uuid5
from app.models.predicates import Predicate, Filter, and_, or_
# from sentence_transformers import SentenceTransformer
import os
from datetime import date, datetime, timedelta
from PIL import Image
from io import BytesIO
import base64
from app.services.ivectordb_client import IVectorDBClient
from app.services.weaviate_vectordb_client import WeaviateVectorDBClient
from automapper import mapper
from app.DAL.database import Database, get_connection_string
from app.DAL.repositories import DogWithImagesRepository

logger.info("Starting up the dogfinder router")

router = APIRouter(prefix="/dogfinder")
vecotrDBClient: IVectorDBClient
dogWithImagesRepository: DogWithImagesRepository = None
dogWithImagesService: DogWithImagesService = None
embedding_model: Any = None

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
                "name": "sex",
                "dataType": ["text"],
                "description": "The dog's sex"
            },
            {
                "name": "extraDetails",
                "dataType": ["text"],
                "description": "Any extra detail that can be returned to the user to identify the dog"
            },
            {
                "name": "location",
                "dataType": ["text"],
                "description": "The location where the dog was lost or found"
            },
            {
                "name": "type",
                "dataType": ["text"],
                "description": "Found Or Lost"
            },
            {
                "name": "imageBase64",
                "dataType": ["blob"],
                "description": "Image in base64 format"
            },
            {
                "name": "isMatched",
                "dataType": ["boolean"],
                "description": "was the dog found?"
            },
            {
                "name": "dogId",
                "dataType": ["number"],
                "description": "Id of the dog"
            },
            {
                "name": "dogImageId",
                "dataType": ["number"],
                "description": "Id of the dog image"
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
                "description": "Is the dog entry verified?"
            },
            {
                "name": "imageContentType",
                "dataType": ["text"],
                "description": "The image content type"
            },
            {
                "name": "chipNumber",
                "dataType": ["text"],
                "description": "The chip number of the dog if exists"
            },
            {
                "name": "name",
                "dataType": ["text"],
                "description": "The name of the dog if exists"
            }
        ]
    }


@router.on_event("startup")
async def startup_event():
    """
    Load all the necessary models and data once the server starts.
    """
    global vecotrDBClient
    global dogWithImagesRepository
    global dogWithImagesService
    global embedding_model

    # Create the vector db client, connecting to the weaviate instance
    vecotrDBClient = WeaviateVectorDBClient(url=f"{os.getenv('WEAVIATE_HOST', 'http://localhost:8080')}")
    # Create the schema
    vecotrDBClient.create_schema(class_name="Dog", class_obj=dog_class_definition)

    # DB variables
    DB_USER = os.environ.get("DB_USER")
    DB_PASSWORD = os.environ.get("DB_PASSWORD")
    DB_HOST = os.environ.get("DB_HOST")
    DB_PORT = os.environ.get("DB_PORT")
    DB_NAME = os.environ.get("DB_NAME", 'dogfinder')
    DB_PROTOCOL = os.environ.get("DB_PROTOCOL", "sqlite")

    db_connection_string = get_connection_string(user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT, db=DB_NAME, protocol=DB_PROTOCOL)
    db = Database(db_url=db_connection_string)
    db.create_database()
    
    # Create the embedding model
    logger.info(f"Creating embedding model")
    embedding_model, cache_info = create_embedding_model()
    
    # Create vectordb indexer
    vectorDBIndexer = VectorDBIndexer(vecotrDBClient, embedding_model)

    # Create the dogWithImagesRepository with the session_factory
    dogWithImagesRepository = DogWithImagesRepository(session_factory=db.session)
    dogWithImagesService = DogWithImagesService(dogWithImagesRepository, vectorDBIndexer)

    


auth = VerifyToken()

# @router.post("/query/", response_model=APIResponse)
# async def query(type: DogType = Form(...), breed: Optional[str] = Form(None), img: UploadFile = File(...), top: int = Form(10), isVerified: Optional[bool] = Form(True)):
#     try:
#         # Handle the image, resize it and convert it to base64 with webp format and get the content type
#         base64Images, imageContentTypes = zip(*handle_uploaded_images([img]))

#         # Create QueryRequest
#         queryRequest = QueryRequest(type=type, breed=breed, imageBase64=base64Images[0], top=top, isVerified=isVerified)

#         # Query the database
#         results = query_vector_db(queryRequest, query)

#         api_response = APIResponse(status_code=200, message=f"Queried {len(results)} results from the vecotrdb", data={ "total": len(results), "results": results })
#     except Exception as e:
#         logger.error(f"Error while querying the vecotrdb: {e}")
#         api_response = APIResponse(status_code=500, message=f"Error while querying the vecotrdb: {e}", data={ "total": 0, "results": [] })
#     finally:
#         # return back a json response and set the status code to api_response.status_code
#         return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.post("/search_in_found_dogs", response_model=APIResponse)
async def search_in_found_dogs(dogSearchRequest: DogSearchRequest):
    try:
        # Handle the image, resize it and convert it to base64 with webp format and get the content type
        base64Images, imageContentTypes = zip(*handle_uploaded_images([dogSearchRequest.base64Image]))

        # Create QueryRequest
        dogSearchRequest.type = DogType.FOUND
        dogSearchRequest.base64Image = base64Images[0]
        dogSearchRequest.isVerified = True

        # Query the database
        results = query_vector_db(dogSearchRequest)

        api_response = APIResponse(status_code=200, message=f"Queried {len(results)} results from the vecotrdb", data={ "total": len(results), "results": results })
    except Exception as e:
        logger.error(f"Error while querying the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the vecotrdb: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.post("/search_in_lost_dogs", response_model=APIResponse)
async def search_in_lost_dogs(dogSearchRequest: DogSearchRequest):
    try:
        # Handle the image, resize it and convert it to base64 with webp format and get the content type
        base64Images, imageContentTypes = zip(*handle_uploaded_images([dogSearchRequest.base64Image]))

        # Create QueryRequest
        dogSearchRequest.type = DogType.LOST
        dogSearchRequest.base64Image = base64Images[0]
        dogSearchRequest.isVerified = True
        # queryRequest = QueryRequest(type=DogType.LOST, breed=breed, imageBase64=base64Images[0], top=top, isVerified=True)

        # Query the database
        results = query_vector_db(dogSearchRequest)

        api_response = APIResponse(status_code=200, message=f"Queried {len(results)} results from the vecotrdb", data={ "total": len(results), "results": results })
    except Exception as e:
        logger.error(f"Error while querying the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the vecotrdb: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.get("/get_unverified_documents", response_model=APIResponse)
async def get_unverified_documents(auth_result: str = Security(auth.verify, scopes=['read:unverified_documents'])):
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
@router.get("/get_dog_by_id", response_model=APIResponse)
async def get_dog_by_id(dogId: int):
    try:
        # Query the database
        dog = dogWithImagesRepository.get_dog_with_images_by_id(dogId)
        
        dogResponseViewModel = mapper.to(DogResponse).map(dog, fields_mapping={"images": []})
        dogResponseViewModel.images = [mapper.to(DogImageResponse).map(image) for image in dog.images]

        api_response = APIResponse(status_code=200, message=f"Queried dog from the database", data={ "results": dogResponseViewModel.model_dump() })
    except Exception as e:
        logger.error(f"Error while querying the database: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the database: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

# Endpoint for quering the database without the need for a query image, only DOG_ID_FIELD
@router.get("/get_dog_by_id_full_details", response_model=APIResponse)
async def query_by_dog_id(dogId: int, auth_result: str = Security(auth.verify, scopes=['read:get_dog_by_id_full_details'])):
    try:
        # Query the database
        dog = dogWithImagesRepository.get_dog_with_images_by_id(dogId)
        
        dogResponseViewModel = mapper.to(DogFullDetailsResponse).map(dog, fields_mapping={"images": []})
        dogResponseViewModel.images = [mapper.to(DogImageResponse).map(image) for image in dog.images]

        api_response = APIResponse(status_code=200, message=f"Queried dog from the database", data={ "results": dogResponseViewModel.model_dump() })
    except Exception as e:
        logger.error(f"Error while querying the database: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while querying the database: {e}", data={ "total": 0, "results": [] })
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

@router.post("/add_document", response_model=APIResponse)
async def add_document(dogRequest: DogAddRequest):
    # logger.info(f"Document Request: {documentRequest}")

    try:
        # Handle the image, resize it and convert it to base64 with webp format and get the content type
        # Unzip the array of tuples coming back from handle_uploaded_images
        base64Images = handle_uploaded_images(dogRequest.base64Images)
        # base64Images, imageContentTypes = zip(*handle_uploaded_images(imgs))

        # Create DogDocument
        # Map the DogRequest to DogDTO
        dogDTO = mapper.to(DogDTO).map(dogRequest, fields_mapping={
            "images": []
        })
        dogDTO.images = [DogImageDTO(base64Image=base64Image[0], imageContentType=base64Image[1]) for base64Image in base64Images]

        # dogDTO = DogDTO(type=type.value,
        #                 images=[DogImageDTO(id=None, base64Image=base64Image[0], imageContentType=base64Image[1]) for base64Image in base64Images],
        #                 contactName=contactName, 
        #                 contactPhone=contactPhone, 
        #                 contactEmail=contactEmail, 
        #                 contactAddress=contactAddress,
        #                 isVerified=IS_VERIFIED_FIELD_DEFAULT_VALUE,
        #                 breed=breed,
        #                 size=size,
        #                 color=color,
        #                 sex=sex,
        #                 extraDetails=extraDetails,
        #                 chipNumber=chipNumber,
        #                 location=location,
        #                 dogFoundOn=dogFoundOn
        # )

        dogDTO, result = dogWithImagesService.add_dog_with_images(dogDTO)

        api_response = APIResponse(status_code=200, message=f"Added documents to the vecotrdb", data=dogDTO.model_dump(), meta=result)
    except Exception as e:
        logger.error(f"Error while adding documents to the vecotrdb: {e}")
        api_response = APIResponse(status_code=500, message=f"Error while adding documents to the vecotrdb: {e}")
    finally:
        # return back a json response and set the status code to api_response.status_code
        return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

# @router.post("/add_documents", response_model=APIResponse)
# async def add_documents(documentRequest: DocumentRequest):
#     logger.info(f"Document Request: {documentRequest}")

#     try:
#         # Add the documents to the database
#         documents_length = len(documentRequest.documents)
#         logger.info(f"Adding {documents_length} documents")
#         documents = []

#         # Create the embedding model
#         logger.info(f"Creating embedding model")
#         embedding_model, cache_info = create_embedding_model()

#         # Set every new document to not verified
#         for document in documentRequest.documents:
#             document.isVerified = IS_VERIFIED_FIELD_DEFAULT_VALUE
#             document.imageBase64, document.imageContentType = resize_image_and_convert_to_format(document.imageBase64, (500, 500))

#         # Embed the documents images
#         embedding_results = embed_documents([create_pil_image(document.imageBase64) for document in documentRequest.documents], embedding_model)
#         request_dog_id = generate_dog_id()

#         # Iterate over the documents and add them to the database
#         for i, document in enumerate(documentRequest.documents):
#             try:
#                 # Create the data object
#                 data_properties = create_data_properties(document, request_dog_id)

#                 # Create a uuid based on the filename
#                 data_properties[UUID_FIELD] = generate_uuid5({"breed": document.breed, "type": document.type, "imageHash": hash_image(document.imageBase64)})

#                 # Embed the document
#                 logger.info(f"Setting Embedding document [{document.filename}] {i+1} of {documents_length}")
#                 data_properties["document_embedding"] = embedding_results[i]


#                 documents.append(data_properties)
#             except Exception as e:
#                 logger.error(f"Error while creating data_properties for document [{document.filename}] {i+1} of {documents_length}: {e}")

#         # Add the documents to the database
#         logger.info(f"Add documents batch")
#         result = vecotrDBClient.add_documents_batch("Dog", documents)

#         api_response = APIResponse(status_code=200, message=f"Added documents to the vecotrdb", data=result)
#     except Exception as e:
#         logger.error(f"Error while adding documents to the vecotrdb: {e}")
#         api_response = APIResponse(status_code=500, message=f"Error while adding documents to the vecotrdb: {e}")
#     finally:
#         # return back a json response and set the status code to api_response.status_code
#         return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

# Add an endpoint to set isVerified to True
@router.post("/verify_document", response_model=APIResponse)
async def verify_document(dogId: int, auth_result: str = Security(auth.verify, scopes=['write:verify_document'])):
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

@router.post("/doc_matched", response_model=APIResponse)
async def doc_matched(foundRequest: DogMatchedRequest, auth_result: str = Security(auth.verify, scopes=['write:dog_matched'])):

    result = dogWithImagesService.update_dog_is_matched(foundRequest.dogId, True)

    api_response = APIResponse(status_code=200, message=f"Dog marked as matched", data={ "results": result })
    # return back a json response and set the status code to api_response.status_code
    return JSONResponse(content=api_response.to_dict(), status_code=api_response.status_code)

# build a predicate for the properties, breed, type, if they are not None with and_ between them
@timeit
def build_filter(dogSearchRequest: DogSearchRequest) -> Optional[Filter]:
    logger.info(f"Building the filter for queryRequest: {dogSearchRequest}")

    # create a list of predicates
    predicates = []

    # add type predicate
    if dogSearchRequest.type is not None:
        predicates.append(Predicate(["type"], "Equal", dogSearchRequest.type.value, FilterValueTypes.valueText))

    # add breed predicate
    if dogSearchRequest.breed is not None:
        predicates.append(Predicate(["breed"], "Equal", dogSearchRequest.breed, FilterValueTypes.valueText))

    # add sex predicate
    if dogSearchRequest.sex is not None:
        predicates.append(Predicate(["sex"], "Equal", dogSearchRequest.sex.value, FilterValueTypes.valueText))

    # add size predicate
    if dogSearchRequest.size is not None:
        predicates.append(Predicate(["size"], "Equal", dogSearchRequest.size.value, FilterValueTypes.valueText))

    # add color predicate
    if dogSearchRequest.color is not None:
        predicates.append(Predicate(["color"], "Equal", dogSearchRequest.color.value, FilterValueTypes.valueText))

    # add chipNumber predicate
    if dogSearchRequest.chipNumber is not None:
        predicates.append(Predicate(["chipNumber"], "Equal", dogSearchRequest.chipNumber, FilterValueTypes.valueText))

    # add name predicate
    if dogSearchRequest.name is not None:
        predicates.append(Predicate(["name"], "Equal", dogSearchRequest.name, FilterValueTypes.valueText))

    # add location predicate
    if dogSearchRequest.location is not None:
        predicates.append(Predicate(["location"], "Equal", dogSearchRequest.location, FilterValueTypes.valueText))

    # add isVerified predicate
    # Commented out because we want to return verified and unverified dogs for now until we have a way to verify them
    # if queryRequest.isVerified is not None:
    #     predicates.append(Predicate(["isVerified"], "Equal", queryRequest.isVerified, FilterValueTypes.valueBoolean))

    # add isMatched predicate, we only want to return dogs that are not matched
    predicates.append(Predicate(["isMatched"], "Equal", False, FilterValueTypes.valueBoolean))

    # if there are predicates return and_ between them
    if (len(predicates) > 0):
        return and_(*predicates)

    # if there are no predicates return None
    return None



def query_vector_db(dogSearchRequest: DogSearchRequest):
    # Open the image from the base64 string to PIL Image
    query_image = create_pil_images([dogSearchRequest.base64Image])[0]

    # Embed the query image
    query_embedding = embed_query(query_image, embedding_model)

    # Build the filter with conditions to query the vector db
    filter = build_filter(dogSearchRequest)

    # Query the database
    logger.info(f"Querying the database")
    results = vecotrDBClient.query(class_name="Dog", query_embedding=query_embedding, limit=dogSearchRequest.top, offset=None, filter=filter.to_dict(), properties=dogSearchRequest.return_properties)

    return results

def handle_uploaded_images(imgs):
    """
    This function takes a list of uploaded images and returns a list of resized and converted images in base64 format and their content types.

    Args:
        imgs: A list of uploaded images.

    Returns:
        A list of tuples containing the resized and converted images in base64 format and their content types.
    """
    images = []
    for img in imgs:
        # Check if file is an UploadFile image or base64 image
        if isinstance(img, UploadFile):
            # Read the image content from the uploaded file
            img_content = img.file.read()

            # Convert the image content to base64 format
            img_base64 = get_base64(img_content)
        else:
            img_base64 = img

        # Resize the image and convert it to the desired format
        img_base64, img_content_type = resize_image_and_convert_to_format(img_base64, (1024, 1024))

        # Append the resized and converted image and its content type to the list
        images.append((img_base64, img_content_type))

    # Return the list of resized and converted images and their content types
    return images
