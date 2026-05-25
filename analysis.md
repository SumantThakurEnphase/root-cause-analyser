## 🔍 Root Cause Analysis

**Error:** `TypeError: Cannot read properties of undefined (reading 'version')` during DWG download.

**Root Cause:**
In `libs/greenthink/index.js`, the `getSiteplan` function calls `getSiteGeometry(metadata, project)` without the `await` keyword.

Because `getSiteGeometry` is an asynchronous function that returns a Promise, spreading it (`{ ...getSiteGeometry(metadata, project) }`) results in an empty object (since Promises do not have enumerable own properties). Consequently, the `siteGeometry` payload sent to the greenthink service only contains the `visibility` property and is missing all actual geometry data. This causes the downstream `greenthink-service` to fail with a `TypeError` when it attempts to read the `version` property of the undefined/missing geometry data.

**Affected File(s):**

- `solargraf-api/libs/greenthink/index.js` — `getSiteplan` function

**Evidence from Logs:**

- `[ERROR] [solargraf-gateway] Object deserialization failed in greenthink service. Error message: 'Site geometry version not provided'`
- `[ERROR] [greenthink-service] GreenThinkDrawing.transformSiteGeometry failed: Cannot read properties of undefined (reading 'version')` at `libs/greenthink/GreenThinkDrawing.js:142`

**Suggested Fix:**
Await the asynchronous `getSiteGeometry` function before spreading its properties inside `getSiteplan`:

```javascript
// solargraf-api/libs/greenthink/index.js

    async getSiteplan(
      context,
      metadata,
      companyResource,
      project,
      excludedContents = [],
      sitePlanFormat = gtdConstants.formats.DWG
    ) {
      const visibility = {
          roofFacets: !excludedContents.includes('roofFacets'),
          obstructions: !excludedContents.includes('obstructions'),
          panels: !excludedContents.includes('panels'),
          setbacks: !excludedContents.includes('setbacks') || !excludedContents.includes('pathways'),
          keepouts: !excludedContents.includes('keepouts'),
          trees: !excludedContents.includes('trees'),
      };

      // Fix: Await the async getSiteGeometry function before spreading
      const siteGeometry = { ...(await getSiteGeometry(metadata, project)), visibility };

      const sitePlanReq = {
          siteGeometry,
          version: gtdConstants.payloadVersion.v3,
          sitePlanFormat,
      };

      return request(context, 'POST', '/siteplan', sitePlanReq, companyResource);
    },
```

**Confidence:** High

**Additional Notes:**
This is a classic Node.js async bug pattern where spreading a Promise object silently fails to copy any properties, resulting in downstream services receiving incomplete payloads. Always ensure functions returning Promises are fully resolved with `await` before using object spread syntax.
