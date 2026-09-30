// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Copyright (c) 2009-2023, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 */

// MDX deployment on NGC prod may happen for per user basis,
// thus changing the URL paths to the different services.
// Endpoints URLs for Web API/Media Streaming etc. used by the
// UI need to built from the UI URL context. Additionally, the
// UI static files path changes as those may not be served at '/'.
// This script post processes generated index.html to accomodate
// the static file changes.

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const xss = require("xss");

// Create a new XSS filter instance
const xssFilter = new xss.FilterXSS();

const scriptContentTemplate = `
  // NOTE: similar in nv-common package js/Constants.js
  var prodBaseUrlPath = window.location.pathname;
  var modifiedLocation = ''
  console.log("try1", window.location, prodBaseUrlPath, modifiedLocation)
  // if path was empty, don't prepend anything to the new paths
  if (prodBaseUrlPath === '/' || window.location.port === "3000" || window.location.port === "8003") {
    console.log("stage1")
    prodBaseUrlPath = '';
    modifiedLocation = window.location.origin + "/calibration"

  } else if (prodBaseUrlPath.includes("/calibration") && window.location.port != 8003) { // why did we only do this?
    console.log("stage2")
    prodBaseUrlPath = window.location.pathname.split("/calibration")[0] + "/calibration"
    modifiedLocation = window.location.origin + window.location.pathname.split("/calibration")[0] + "/calibration"
  }
  console.log("try2", prodBaseUrlPath, modifiedLocation)


  // Add all link elements with href containing /static/
  var linkElementPaths = LINK-ELEMENT-URLS;

  linkElementPaths.forEach(function (path) {
    if (!path.includes(".css")){
      const linkElement = document.createElement('link');
      linkElement.href = prodBaseUrlPath + path;
      linkElement.rel = "stylesheet";
      document.head.appendChild(linkElement);
    } else{
      //modify css files static paths
      // Fetch the CSS file using fetch API
      console.log("modify css", window.location.origin, window.location.pathname.split("/calibration")[0], path )
      fetch(modifiedLocation + path)
          .then(function(response) {
            return response.text();
          })
          .then(function(cssContent) {
              // Prepend the URL prefix + "/static" to URLs starting with "/static" in the CSS content
              const modifiedCss = cssContent.replace(/url\\((\\/static.*?)\\)/g, 'url(' + prodBaseUrlPath + '$1)');

              // Create a <style> tag with the modified CSS content
              const styleTag = document.createElement('style');
              styleTag.textContent = modifiedCss;

              // Append the <style> tag to the <head> element
              document.head.appendChild(styleTag);
          })
          .catch(function(error) {
              console.error('Error fetching or modifying CSS file:', error);
          });

    }
  });

  // Add all script elements with src containing /static/
  var scriptElementPaths = SCRIPT-ELEMENT-URLS;

  scriptElementPaths.forEach(function (path) {
    // Append a new script element with the modified source path

    const newScript = document.createElement('script');

    newScript.src = prodBaseUrlPath + path;
    newScript.defer = "defer";
    // change next line document.head.
    document.head.appendChild(newScript);

  });
`;

// Read the original index.html content. Assuming it remains at app's root build folder
const indexPath = path.join("build", "index.html");
const indexContent = fs.readFileSync(indexPath, "utf8");

// Create a DOM from the index.html content
const dom = new JSDOM(indexContent);
const document = dom.window.document;

// Collect all relevant link/script tag src paths to be modified
// and dynamically added on page load

// Find all link elements with href containing /static/
var linkElementPaths = [];
var linkElements = document.querySelectorAll('link[href*="/static/"]');

// Loop through the link elements and update their href attribute
linkElements.forEach(function (linkElement) {
  // Update the href attribute of the link element
  linkElementPaths.push(linkElement.href);
  linkElement.remove();
});

// Find all script elements with src containing /static/
var scriptElementPaths = [];
var scriptElements = document.querySelectorAll('script[src*="/static/"]');

// Loop through the script elements and update their src attribute
scriptElements.forEach(function (scriptElement) {
  // Update the src attribute of the script element
  scriptElementPaths.push(scriptElement.src);
  scriptElement.remove();
});

var scriptContent = scriptContentTemplate.replace(
  "LINK-ELEMENT-URLS",
  JSON.stringify(linkElementPaths)
);
scriptContent = scriptContent.replace(
  "SCRIPT-ELEMENT-URLS",
  JSON.stringify(scriptElementPaths)
);

// Create a script element and set it to sanitized content
var scriptElement = document.createElement("script");
scriptElement.textContent = xssFilter.process(scriptContent);

// Append sanitized script to the end of the body
document.body.appendChild(scriptElement);

// Write the modified content to the build/index.html file
fs.writeFileSync(indexPath, dom.serialize(), "utf8");

console.log(
  "Modified index.html to include NGC prod path for static files generated successfully."
);

