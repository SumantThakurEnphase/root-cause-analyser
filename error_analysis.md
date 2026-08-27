## 🔍 Root Cause Analysis

**Cause Category:** Data Issue

### Error 1: TypeError: Cannot read properties of undefined (reading 'map') in `intersectPathwayAndSetbacksPolygons.ts`

**Root Cause:** The application is attempting to process site geometry for a permit plan, but the geometry data (specifically related to setbacks or pathways) is missing or malformed for this project. The function `addSetbacksAndPathwaysBoundaries` expects an array to iterate over, but the source object is `undefined`, causing the `map` (or `forEach`) operation to crash. This is likely due to a design that lacks defined setbacks or has an incomplete geometry state in the database.

**Affected File(s):**
- `libs/greenthink/utils/intersectPathwayAndSetbacksPolygons.ts` — `addSetbacksAndPathwaysBoundaries` (line 241)

**Evidence from Logs:**
- `Message: 5xx Server Error: Cannot read properties of undefined (reading 'map')`
- `at addSetbacksAndPathwaysBoundaries (/home/node/code/libs/greenthink/utils/intersectPathwayAndSetbacksPolygons.ts:241:30)`

**Suggested Fix:**
Add a defensive check to ensure the geometry data exists before attempting to iterate over it.
```typescript
// In intersectPathwayAndSetbacksPolygons.ts
function addSetbacksAndPathwaysBoundaries(data) {
  if (!data || !data.pathways) { // Add safety check
    return; 
  }
  data.pathways.forEach(...);
}
```

---

### Error 2: Object deserialization failed due to missing 'necYear' property

**Root Cause:** The `GtdWebApi` (likely an external or internal service handling full design generation) is failing to deserialize the request payload because the `customizations.ahj` object is missing the required `necYear` field. This indicates that the project settings for this specific design are stale or were created before `necYear` became a mandatory field in the AHJ (Authority Having Jurisdiction) configuration.

**Affected File(s):**
- `GtdWebApi/Controllers/FullDesignController.cs` — `Post` method (line 90)

**Evidence from Logs:**
- `Message: Object deserialization failed. Error message: 'Required property 'necYear' not found in JSON. Path 'customizations.ahj', line 1, position 64895.'`

**Suggested Fix:**
The project's AHJ configuration needs to be updated to include a valid `necYear`. You can run a script to patch the project settings for Project ID `3380452`:
```javascript
// Example migration/patch logic
const projectSettings = await db.ProjectSettings.findOne({ where: { project_id: 3380452 } });
if (projectSettings.customizations.ahj && !projectSettings.customizations.ahj.necYear) {
    projectSettings.customizations.ahj.necYear = '2020'; // Default to a standard year
    await projectSettings.save();
}
```

---

**Confidence:** High

**Additional Notes:** 
The two errors are linked: the `500` error in the `solargraf-gateway` is the result of the `GtdWebApi` failing to process the request due to the missing `necYear` data. The `TypeError` in the TypeScript library is likely a secondary failure or a result of the service attempting to handle the incomplete/failed response object. Resolving the missing `necYear` in the database should resolve the primary blocker for the permit package download.