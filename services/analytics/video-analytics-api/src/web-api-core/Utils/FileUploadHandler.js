/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

'use strict';

const InternalServerError = require('../Errors/InternalServerError');
const InvalidInputError = require('../Errors/InvalidInputError');
const BadRequestError = require('../Errors/BadRequestError');
const multer = require('multer');
const path = require('path');

/** 
 * Class which defines FileUploadHandler
 * @memberof mdxWebApiCore.Utils
 * */

class FileUploadHandler {

    /**
      * handles errors which occur during file upload
      * @public
      * @static
     * @param {Error} error - Upload error object.
     * @param {Array<{name:string, maxCount:number, validFileExtensions: Set<string>}>} formFields - Form fields configured for upload validation.
      * @example
      * const mdx = require("@nvidia-mdx/web-api-core");
      * mdx.Utils.FileUploadHandler.errorHandler(error,formFields);
     */
    static errorHandler(error, formFields){
        if (error instanceof InvalidInputError) {
            throw error;
        }else if (error instanceof multer.MulterError) {
            if (error.message === "Too many files") {
                let formFieldsSizeRestriction = formFields.map(formField => `${formField.name}: ${formField.maxCount}`);
                let errorMessage = `Too many files have been uploaded. Following are the limits- ${formFieldsSizeRestriction.join(", ")}.`;
                throw new BadRequestError(errorMessage);
            } else {
                console.error(error);
                throw new BadRequestError(error.message);
            }
        } else {
            throw new InternalServerError(error.message);
        }
    }

    /**
      * returns modified file name 
      * @public
      * @static
     * @param {string} originalFileName - Original uploaded file name.
      * @returns {string} - Modified file name is returned
      * @example
      * const mdx = require("@nvidia-mdx/web-api-core");
      * let modifiedFileName = mdx.Utils.FileUploadHandler.getModifiedFileName("abc.png");
      */
    static getModifiedFileName(originalFileName){
        const uniqueSuffix = `${Date.now()} - ${Math.round(Math.random() * 1E9)}`;
        let ext = path.extname(originalFileName).toLowerCase();
        let splitFileName = originalFileName.split(ext);
        splitFileName.pop();
        let modifiedFileName=`${splitFileName.join(ext)} - ${uniqueSuffix}${ext}`;
        return modifiedFileName;
    }

    /**
      * returns the result of Multer.fields which is used to obtain the uploaded files
      * @public
      * @static
     * @param {Array<{name:string, maxCount:number, validFileExtensions: Set<string>}>} formFields - Form fields configured for upload validation.
     * @param {string} fileUploadLocation - Directory used to store uploaded files.
     * @param {boolean} [modifyFileName=true] - Whether to modify uploaded file names.
      * @returns {Object} Result of Multer.fields is returned
      */
    static getMulterUpload(formFields, fileUploadLocation, modifyFileName = true){
        const multerFormFields = new Array();
        const fieldNameValidExtensionsMap = new Map();
        for(let formField of formFields){
            multerFormFields.push({name:formField.name, maxCount: formField.maxCount});
            if(formField.hasOwnProperty("validFileExtensions")){
                fieldNameValidExtensionsMap.set(formField.name,formField.validFileExtensions);
            }
        }
        const multerStorage = multer.diskStorage({
            destination: (req, file, callback) => {
                callback(null, fileUploadLocation);
            },
            filename: (req, file, callback) => {
                let fileName = (modifyFileName)?FileUploadHandler.getModifiedFileName(file.originalname) : file.originalname;
                callback(null, fileName);
            }
        });
        let upload = multer({
            storage: multerStorage,
            fileFilter: (req, file, cb) => {
                let fieldName = file.fieldname;
                if(fieldNameValidExtensionsMap.has(fieldName)){
                    const validFileExtensions=fieldNameValidExtensionsMap.get(fieldName);
                    let ext = path.extname(file.originalname).toLowerCase();
                    if (!validFileExtensions.has(ext)) {
                        let errorMessage = `Invalid file: ${file.originalname}. Only files with following extensions are allowed: ${Array.from(validFileExtensions).join(", ")}.`;
                        return cb(new InvalidInputError(errorMessage), false);
                    }
                }            
                cb(null, true);
            }
        }).fields(multerFormFields);
        return upload;
    }

}

module.exports = FileUploadHandler;