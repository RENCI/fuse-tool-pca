import datetime
import io
import json
import logging
import os
import pathlib
import traceback
from datetime import datetime
from logging.config import dictConfig

import numpy as np
import pandas as pd
import requests
import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sklearn.decomposition import PCA

from fuse.models.Config import LogConfig
from fuse.models.Objects import ToolParameters

dictConfig(LogConfig().dict())
logger = logging.getLogger("fuse-tool-pca")

g_api_version = "0.0.1"

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
    f"http://localhost:{os.getenv('HOST_PORT')}",
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


# API is described in:
# http://localhost:8083/openapi.json

# Therefore:
# This endpoint self-describes with:
# curl -X 'GET'    'http://localhost:8083/openapi.json' -H 'accept: application/json' 2> /dev/null |python -m json.tool |jq '.paths."/submit".post.parameters' -C |less
# for example, an array of parameter names can be retrieved with:
# curl -X 'GET'    'http://localhost:8083/openapi.json' -H 'accept: application/json' 2> /dev/null |python -m json.tool |jq '.paths."/submit".post.parameters[].name' 


@app.post("/submit", description="Submit an analysis")
async def analyze(parameters: ToolParameters = Depends(ToolParameters.as_form),
                  expression_file: UploadFile = File(default=None, description="Gene Expression Data (csv)")):
    """
    Gene expression data are formatted with gene id's on rows, and samples on columns. Gene expression counts/intensities will not be normalized as part of the analysis. No header row, comma-delimited.
    """
    logger.info(f"parameters: {parameters}")
    try:
        start_time = datetime.now()
        logger.info(f"started: {start_time}")
        # do some analysis

        match (expression_file, parameters.expression_url):
            case (None, url):
                r = requests.get(parameters.expression_url)
                logger.info("getting expression_stream")
                gene_expression_stream = io.StringIO(str(r.content, 'utf-8'))
            case (file, None):
                gene_expression_string = await file.read()
                gene_expression_stream = io.StringIO(str(gene_expression_string, 'utf-8'))
            case (None, None):
                raise Exception("expression_file & parameters.expression_url can't both be None")

        logger.info("reading expression streem")
        gene_expression_df = pd.read_csv(gene_expression_stream, sep=",", dtype=np.float64)
        logger.info("read input file.")
        df_pca = PCA(n_components=parameters.number_of_components)
        logger.info("set up PCA.")
        df_principalComponents = df_pca.fit_transform(gene_expression_df.T)
        logger.info("fit the transform.")
        pc_cols = []
        for col in range(0, parameters.number_of_components):
            pc_cols.append(f'PC{col + 1}')
        df_results = pd.DataFrame(data=df_principalComponents, columns=pc_cols)
        logger.info("added PC column names.")
        results = df_results.values.tolist()
        type_converted = [str(a) for a in results]
        logger.info("transformed to list.")
        # analysis finished.
        end_time = datetime.now()
        logger.info(f"ended: {end_time}")
        # xxx come back to this
        # return_object = AnalysisResults()
        return_object = {
            "submitter_id": parameters.submitter_id,
            "start_time": start_time,
            "end_time": end_time,
            "results": [
                {
                    "name": "pca",
                    "results_type": "filetype_results_PCATable",
                    "spec": "",
                    "dimension": [len(results), parameters.number_of_components],
                    "data": type_converted
                }
            ]}

        logger.info(msg=f"returning: {return_object}")
        return return_object
    except Exception as e:
        detail_str = f"! Exception {type(e)} occurred while running submit, message=[{e}] ! traceback={traceback.format_exc()}"
        logger.error(detail_str)
        raise HTTPException(status_code=404, detail=detail_str)


@app.get("/service-info", summary="Retrieve information about this service")
async def service_info():
    """
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
    """
    service_info_path = pathlib.Path(__file__).parent / "service_info.json"
    with open(service_info_path) as f:
        return json.load(f)


if __name__ == '__main__':
    uvicorn.run("main:app", host='0.0.0.0', port=int(os.getenv("HOST_PORT")), reload=True)
