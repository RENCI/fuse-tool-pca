import uvicorn
import datetime
import inspect
import os

from fastapi import FastAPI, File, UploadFile, Form, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, AnyHttpUrl
from typing import Type, Optional, List
from starlette.responses import StreamingResponse

from bson.json_util import dumps, loads
import traceback


from fuse.models.Objects import AnalysisResults

from fastapi.logger import logger

from logging.config import dictConfig
import logging
from fuse.models.Config import LogConfig

dictConfig(LogConfig().dict())
logger = logging.getLogger("fuse-tool-pca")

g_api_version="0.0.1"

app = FastAPI(openapi_url=f"/api/{g_api_version}/openapi.json",
              title="PCA Tool",
              description="Fuse-certified Tool for performing principal component analysis (PCA). Can stand alone or be plugged into multiple data sources using http://github.com/RENCI/fuse-agent.",
              version=g_api_version,
              terms_of_service="https://github.com/RENCI/fuse-agent/doc/terms.pdf",
              contact={
                  "name": "Maintainer(Kimberly Robasky)",
                  "url": "http://txscience.renci.org/contact/",
                  "email": "kimberly.robasky@gmail.com"
            },
            license_info={
            "name": "MIT License",
                "url": "https://github.com/RENCI/fuse-tool-pca/blob/main/LICENSE"
            }
              )

origins = [
    f"http://{os.getenv('HOST_NAME')}:{os.getenv('HOST_PORT')}",
    f"http://{os.getenv('HOST_NAME')}",
    "http://localhost:{os.getenv('HOST_PORT')}",
    "http://localhost",
    "*",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import pathlib

import json
import io
import requests
    
# API is described in:
# http://localhost:8083/openapi.json

# Therefore:
# This endpoint self-describes with:
# curl -X 'GET'    'http://localhost:8083/openapi.json' -H 'accept: application/json' 2> /dev/null |python -m json.tool |jq '.paths."/submit".post.parameters' -C |less
# for example, an array of parameter names can be retrieved with:
# curl -X 'GET'    'http://localhost:8083/openapi.json' -H 'accept: application/json' 2> /dev/null |python -m json.tool |jq '.paths."/submit".post.parameters[].name' 

from datetime import datetime
import requests
@app.post("/submit", description="Submit an analysis")
async def analyze(submitter_id: str = Query(default=..., description="unique identifier for the submitter (e.g., email)"),
                  number_of_components: int = Query(default=None, description="Number of principle components to return."),
                  expression_url: AnyHttpUrl = Query(default=None, description="either submit an url to the object to be analyzed or upload a file, but not both"),
                  expression_file: UploadFile = File(default=None, description="either submit an url to the object to be analyzed or upload a file, but not both")):
    '''
    Gene expression data are formatted with gene id's on rows, and samples on columns. Gene expression counts/intensities will not be normalized as part of the analysis. No header row, comma-delimited.
    There should be no header on the data file, or it will be treated as a row of gene expression intensitites
    '''
    function_name="[analyze]"
    try:
        # xxx figure out how to assert this in the pydantic validation instead
        assert (expression_url is None or expression_file is None) and (expression_url is not None or expression_file is not None)
        start_time=datetime.now()
        logger.info(msg=f"[submit] started: {start_time}")
        # do some analysis
        if expression_file is not None:
            logger.info(f'{function_name} getting file')
            gene_expression_string = await expression_file.read()
            gene_expression_stream = io.StringIO(str(gene_expression_string,'utf-8'))
        else:
            # xxx this is problematic; can't seem to get url off loclahost when this app is running in container on same network.
            logger.info(f"{function_name} getting url: {expression_url}")
            r = requests.get(expression_url)
            logger.info(f"{function_name} getting expression_stream")
            gene_expression_stream = io.StringIO(str(r.content, 'utf-8'))
        import pandas as pd
        import numpy as np
        logger.info(f"{function_name} reading expression streem")

        
        gene_expression_df = pd.read_csv(gene_expression_stream, sep=",", dtype=np.float64, header=None, skiprows=1)
        logger.info(msg=f"{function_name} read input file.")
        
        from sklearn.decomposition import PCA
        df_pca = PCA(n_components=number_of_components)
        logger.info(msg=f"{function_name} set up PCA.")
        
        # first column is gene id;
        # remove any rows where gene id = 0
        gene_expression_df=gene_expression_df[gene_expression_df.loc[:,0] != 0]
        # remove gene id column
        gene_expression_df= gene_expression_df.loc[:,1:]
        
        df_principalComponents = df_pca.fit_transform(gene_expression_df.T)
        logger.info(msg=f"{function_name} fit the transform.")
        pc_cols=[]
        for col in range(0,number_of_components):
            pc_cols.append(f'PC{col +1}')
        df_results = pd.DataFrame(data = df_principalComponents,
                                      columns = pc_cols)
        logger.info(msg=f"{function_name} added PC column names.")
        results = df_results.values.tolist()
        logger.info(msg=f"{function_name} transformed to list.")
        # analysis finished.
        end_time=datetime.now()
        logger.info(msg=f"{function_name} ended: {end_time}")
        # xxx come back to this
        #return_object = AnalysisResults()
        return_object={}
        return_object["submitter_id"]= submitter_id
        return_object["start_time"] = start_time
        return_object["end_time"] = end_time
        return_object["contents"]= [
            {
                "name": "PCA table",
                "results_type": "PCA",
                "spec": "",
                "size": [len(results), number_of_components],
                "contents": results
            }
        ]

        logger.info(msg=f"{function_name} returning: {return_object}")
        return return_object
    except Exception as e:
        detail_str=f"{function_name} ! Exception {type(e)} occurred while running submit, message=[{e}] ! traceback={traceback.format_exc()}"
        logger.error(msg=detail_str)
        raise HTTPException(status_code=404,
                            detail=detail_str)

@app.get("/service-info", summary="Retrieve information about this service")
async def service_info():
    '''
    Returns information similar to DRS service format

    Extends the v1.0.0 GA4GH Service Info specification as the standardized format for GA4GH web services to self-describe.

    According to the service-info type registry maintained by the Technical Alignment Sub Committee (TASC), a DRS service MUST have:
    - a type.group value of org.ga4gh
    - a type.artifact value of drs

    e.g.
    ```
    {
      "id": "com.example.drs",
      "description": "Serves data according to DRS specification",
      ...
      "type": {
        "group": "org.ga4gh",
        "artifact": "drs"
      }
    ...
    }
    ```
    '''
    service_info_path = pathlib.Path(__file__).parent / "service_info.json"
    with open(service_info_path) as f:
        return json.load(f)


if __name__=='__main__':
        uvicorn.run("main:app", host='0.0.0.0', port=int(os.getenv("HOST_PORT")), reload=True )
