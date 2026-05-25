## 🔍 Root Cause Analysis

**Error:** DWG download from SDT/EDT fails with `TypeError: Cannot read properties of undefined (reading 'version')` and `Site geometry version not provided`.

**Root Cause:**
In `libs/greenthink/index.js`, the `getSiteplan` function calls `getSiteGeometry(metadata, project)` synchronously and spreads its result into the `siteGeometry` object. However, `getSiteGeometry` is an asynchronous function that returns a `Promise`.

Spreading a pending `Promise` object (`{ ...getSiteGeometry(...) }`) results in an empty object because Promises do not have enumerable properties representing their future resolved values. Consequently, the `siteGeometry` payload sent to the GreenThink service only contains the `visibility` property and is missing all actual geometry data (including the `version` field), causing the deserialization and transformation to fail.

**Affected File(s):**

- `solargraf-api/libs/greenthink/index.js` — `getSiteplan`

**Evidence from Logs:**

- `[2025-05-20T14:32:11.300Z] [WARN] [solargraf-gateway] getSiteGeometry returned a pending Promise instead of resolved data. Possible missing await.`
- `[2025-05-20T14:32:11.400Z] [ERROR] [greenthink-service] GreenThinkDrawing.transformSiteGeometry failed: Cannot read properties of undefined (reading 'version')`

**Suggested Fix:**
Add `await` before calling `getSiteGeometry` inside the `getSiteplan` function in `solargraf-api/libs/greenthink/index.js` to ensure the Promise resolves before spreading its properties.

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

      // FIX: Await the asynchronous getSiteGeometry call
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

**Additional Notes:** Since `getSiteplan` is already declared as an `async` function, adding `await` is safe and will not require changing the function signature.
