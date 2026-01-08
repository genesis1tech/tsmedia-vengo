# AWS Lambda Update: Add nfcUrl to openDoor Response

## Overview

The kiosk currently builds the NFC URL locally and makes a separate HTTP call to Convex to create a pending transaction. This is being simplified so that the Lambda provides the complete NFC URL in the `openDoor` response.

## Required Change

Add a new field `nfcUrl` to the `openDoor` response payload.

### Current Response Structure

```json
{
  "thingName": "topper-stopper-001",
  "returnAction": "openDoor",
  "transactionId": "550e8400-e29b-41d4-a716-446655440000",
  "barcode": "889392021431",
  "productName": "Coca-Cola Classic",
  "productBrand": "Coca-Cola",
  "productImage": "https://example.com/images/coke.jpg",
  "itemType": "plastic",
  "itemSize": "12 FL OZ",
  "pointsEarned": 10
}
```

### New Response Structure

```json
{
  "thingName": "topper-stopper-001",
  "returnAction": "openDoor",
  "transactionId": "550e8400-e29b-41d4-a716-446655440000",
  "barcode": "889392021431",
  "productName": "Coca-Cola Classic",
  "productBrand": "Coca-Cola",
  "productImage": "https://example.com/images/coke.jpg",
  "itemType": "plastic",
  "itemSize": "12 FL OZ",
  "pointsEarned": 10,
  "nfcUrl": "https://tsrewards--test.expo.app/hook?scanid=550e8400-e29b-41d4-a716-446655440000&barcode=889392021431"
}
```

## nfcUrl Format

The URL should follow this format:

```
https://tsrewards--test.expo.app/hook?scanid={transactionId}&barcode={barcode}
```

### Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `scanid` | The transaction ID (UUID) | `550e8400-e29b-41d4-a716-446655440000` |
| `barcode` | The scanned barcode | `889392021431` |

### Example Lambda Code (Pseudocode)

```javascript
// In your Lambda handler, when building the openDoor response:

const transactionId = generateOrGetTransactionId(); // UUID
const barcode = event.barcode;

// Build the NFC URL
const nfcUrl = `https://tsrewards--test.expo.app/hook?scanid=${transactionId}&barcode=${barcode}`;

const response = {
  thingName: event.thingName,
  returnAction: 'openDoor',
  transactionId: transactionId,
  barcode: barcode,
  productName: productData.name,
  productBrand: productData.brand,
  productImage: productData.imageUrl,
  itemType: productData.itemType,
  itemSize: productData.size,
  pointsEarned: calculatePoints(productData),
  nfcUrl: nfcUrl  // <-- NEW FIELD
};

// Publish to MQTT topic: {thingName}/openDoor
```

## Backend Responsibility

With this change, the backend should handle any transaction record creation that was previously done by the kiosk's Convex client. The kiosk will:

1. Receive the `openDoor` response with `nfcUrl`
2. Display the product image
3. Open/close the door
4. Broadcast the `nfcUrl` via NFC (no local URL building, no Convex HTTP call)

## What the Kiosk Will Do

Once this Lambda update is deployed, the kiosk will:

- Extract `nfcUrl` from the `openDoor` response
- Pass it directly to the NFC emulator for broadcasting
- No longer call Convex to create pending transactions
- No longer build URLs locally

## Testing

After updating the Lambda:

1. Scan a barcode at the kiosk
2. Verify the `openDoor` response includes `nfcUrl`
3. Verify NFC broadcasts the correct URL
4. Verify the mobile app can read and process the NFC tag

## Notes

- The `nfcUrl` should be a complete, ready-to-use URL (including `https://`)
- The kiosk will broadcast whatever URL is provided (no validation/modification)
- If `nfcUrl` is missing, the kiosk will skip NFC broadcasting (graceful degradation)
