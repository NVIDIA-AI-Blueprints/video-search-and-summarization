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

const { expect } = require('chai');
const Utils = require('../../../../src/web-api-core/Utils/Utils.js');

describe('Utils.getPlaceHierarchy', () => {

    it('should return the correct hierarchy for city, building, and room', () => {
        const place = "city=Santa-Clara/building=Nth_Street_Cafe/room=Entrance";
        const result = Utils.getPlaceHierarchy(place);
        expect(result).to.equal("city/building/room");
    });

    it('should return an empty string when the input is empty', () => {
        const place = "";
        const result = Utils.getPlaceHierarchy(place);
        expect(result).to.equal("");
    });

    it('should return the correct hierarchy for a single place attribute', () => {
        const place = "city=Santa-Clara";
        const result = Utils.getPlaceHierarchy(place);
        expect(result).to.equal("city");
    });

    it('should handle attributes without values', () => {
        const place = "city=/building=Nth_Street_Cafe/room=";
        const result = Utils.getPlaceHierarchy(place);
        expect(result).to.equal("city/building/room");
    });
    
});

describe('Utils.tsCompare', () => {
    it('should return true when timestamp1 is greater than timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T12:00:00Z', '>', '2024-08-14T11:00:00Z');
        expect(result).to.be.true;
    });

    it('should return false when timestamp1 is not greater than timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T10:00:00Z', '>', '2024-08-14T11:00:00Z');
        expect(result).to.be.false;
    });

    it('should return true when timestamp1 is equal to or greater than timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T11:00:00Z', '>=', '2024-08-14T11:00:00Z');
        expect(result).to.be.true;
    });

    it('should return false when timestamp1 is not equal to or greater than timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T10:00:00Z', '>=', '2024-08-14T11:00:00Z');
        expect(result).to.be.false;
    });

    it('should return true when timestamp1 is less than timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T10:00:00Z', '<', '2024-08-14T11:00:00Z');
        expect(result).to.be.true;
    });

    it('should return false when timestamp1 is not less than timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T12:00:00Z', '<', '2024-08-14T11:00:00Z');
        expect(result).to.be.false;
    });

    it('should return true when timestamp1 is equal to or less than timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T11:00:00Z', '<=', '2024-08-14T11:00:00Z');
        expect(result).to.be.true;
    });

    it('should return false when timestamp1 is not equal to or less than timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T12:00:00Z', '<=', '2024-08-14T11:00:00Z');
        expect(result).to.be.false;
    });

    it('should return true when timestamp1 is equal to timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T11:00:00Z', '==', '2024-08-14T11:00:00Z');
        expect(result).to.be.true;
    });

    it('should return false when timestamp1 is not equal to timestamp2', () => {
        const result = Utils.tsCompare('2024-08-14T12:00:00Z', '==', '2024-08-14T11:00:00Z');
        expect(result).to.be.false;
    });

    it('should throw an error for an invalid comparison operator', () => {
        expect(() => Utils.tsCompare('2024-08-14T12:00:00Z', '!=', '2024-08-14T11:00:00Z'))
            .to.throw("Invalid comparison operator. Valid comparison operators include: '>', '>=', '<', '<=', '=='");
    });

});

describe('Utils.setDifference', () => {
    it('should return an empty set when both sets are empty', () => {
        const set1 = new Set();
        const set2 = new Set();
        const result = Utils.setDifference(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect(result.size).to.equal(0);
    });

    it('should return the first set when the second set is empty', () => {
        const set1 = new Set([1, 2, 3]);
        const set2 = new Set();
        const result = Utils.setDifference(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect(result).to.deep.equal(set1);
    });

    it('should return an empty set when the first set is empty', () => {
        const set1 = new Set();
        const set2 = new Set([1, 2, 3]);
        const result = Utils.setDifference(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect(result.size).to.equal(0);
    });

    it('should return the difference when the sets have unique elements', () => {
        const set1 = new Set([1, 2, 3]);
        const set2 = new Set([2, 3, 4]);
        const result = Utils.setDifference(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect([...result]).to.have.members([1]);
    });

    it('should return an empty set when both sets are identical', () => {
        const set1 = new Set([1, 2, 3]);
        const set2 = new Set([1, 2, 3]);
        const result = Utils.setDifference(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect(result.size).to.equal(0);
    });

});

describe('Utils.setIntersection', () => {
    it('should return an empty set when both sets are empty', () => {
        const set1 = new Set();
        const set2 = new Set();
        const result = Utils.setIntersection(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect(result.size).to.equal(0);
    });

    it('should return an empty set when the first set is empty', () => {
        const set1 = new Set();
        const set2 = new Set([1, 2, 3]);
        const result = Utils.setIntersection(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect(result.size).to.equal(0);
    });

    it('should return an empty set when the second set is empty', () => {
        const set1 = new Set([1, 2, 3]);
        const set2 = new Set();
        const result = Utils.setIntersection(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect(result.size).to.equal(0);
    });

    it('should return the intersection of two sets', () => {
        const set1 = new Set([1, 2, 3]);
        const set2 = new Set([2, 3, 4]);
        const result = Utils.setIntersection(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect([...result]).to.have.members([2, 3]);
    });

    it('should return an empty set when there are no common elements', () => {
        const set1 = new Set([1, 2, 3]);
        const set2 = new Set([4, 5, 6]);
        const result = Utils.setIntersection(set1, set2);
        expect(result).to.be.an.instanceof(Set);
        expect(result.size).to.equal(0);
    });

});

describe('Utils.sleep', () => {
    it('should wait for the specified amount of time', async () => {
        const start = Date.now();
        const waitTime = 100; // milliseconds

        await Utils.sleep(waitTime);

        const end = Date.now();
        const elapsed = end - start;

        expect(elapsed).to.be.at.most(waitTime + 10); // Small margin for timing variations
    });

    it('should handle zero wait time', async () => {
        const start = Date.now();
        const waitTime = 0; // milliseconds

        await Utils.sleep(waitTime);

        const end = Date.now();
        const elapsed = end - start;

        expect(elapsed).to.be.at.most(10); // Small margin for timing variations
    });
});

describe('Utils.getTimeInterval', () => {
    it('should calculate correct time interval in seconds', () => {
        const fromTimestamp = '2023-01-01T10:00:00.000Z';
        const toTimestamp = '2023-01-01T10:00:01.000Z';
        const result = Utils.getTimeInterval(fromTimestamp, toTimestamp);
        expect(result).to.equal(1);
    });

    it('should calculate time interval with millisecond precision', () => {
        const fromTimestamp = '2023-01-01T10:00:00.000Z';
        const toTimestamp = '2023-01-01T10:00:01.234Z';
        const result = Utils.getTimeInterval(fromTimestamp, toTimestamp);
        expect(result).to.equal(1.234);
    });

    it('should handle zero time interval', () => {
        const timestamp = '2023-01-01T10:00:00.000Z';
        const result = Utils.getTimeInterval(timestamp, timestamp);
        expect(result).to.equal(0);
    });

    it('should handle negative time interval (toTimestamp before fromTimestamp)', () => {
        const fromTimestamp = '2023-01-01T10:00:01.000Z';
        const toTimestamp = '2023-01-01T10:00:00.000Z';
        const result = Utils.getTimeInterval(fromTimestamp, toTimestamp);
        expect(result).to.equal(-1);
    });

    it('should handle large time intervals', () => {
        const fromTimestamp = '2023-01-01T00:00:00.000Z';
        const toTimestamp = '2023-01-01T01:00:00.000Z';
        const result = Utils.getTimeInterval(fromTimestamp, toTimestamp);
        expect(result).to.equal(3600); // 1 hour in seconds
    });
});

describe('Utils.deleteFiles', () => {
    const fs = require('fs');
    const path = require('path');
    const os = require('os');

    it('should return success when deleting existing files', async () => {
        // Create temp files
        const tempDir = os.tmpdir();
        const tempFile1 = path.join(tempDir, `test-delete-${Date.now()}-1.txt`);
        const tempFile2 = path.join(tempDir, `test-delete-${Date.now()}-2.txt`);
        
        fs.writeFileSync(tempFile1, 'test content 1');
        fs.writeFileSync(tempFile2, 'test content 2');

        const result = await Utils.deleteFiles([tempFile1, tempFile2]);

        expect(result).to.deep.equal({ success: true });
        expect(fs.existsSync(tempFile1)).to.be.false;
        expect(fs.existsSync(tempFile2)).to.be.false;
    });

    it('should return success when file does not exist (with warning)', async () => {
        const nonExistentFile = '/path/to/nonexistent/file.txt';
        const result = await Utils.deleteFiles([nonExistentFile]);

        expect(result).to.deep.equal({ success: true });
    });

    it('should handle empty file paths array', async () => {
        const result = await Utils.deleteFiles([]);

        expect(result).to.deep.equal({ success: true });
    });

    it('should handle mixed existing and non-existing files', async () => {
        const tempDir = os.tmpdir();
        const tempFile = path.join(tempDir, `test-delete-mixed-${Date.now()}.txt`);
        fs.writeFileSync(tempFile, 'test content');

        const result = await Utils.deleteFiles([tempFile, '/nonexistent/file.txt']);

        expect(result).to.deep.equal({ success: true });
        expect(fs.existsSync(tempFile)).to.be.false;
    });
});

describe('Utils.conditionalAsync', () => {
    it('should await and return value if input is a Promise', async () => {
        const promise = Promise.resolve('resolved value');
        const result = await Utils.conditionalAsync(promise);

        expect(result).to.equal('resolved value');
    });

    it('should return value directly if input is not a Promise', async () => {
        const value = 'plain value';
        const result = await Utils.conditionalAsync(value);

        expect(result).to.equal('plain value');
    });

    it('should handle async function result', async () => {
        const asyncFn = async () => 'async result';
        const result = await Utils.conditionalAsync(asyncFn());

        expect(result).to.equal('async result');
    });

    it('should handle null value', async () => {
        const result = await Utils.conditionalAsync(null);

        expect(result).to.be.null;
    });

    it('should handle object values', async () => {
        const obj = { key: 'value' };
        const result = await Utils.conditionalAsync(obj);

        expect(result).to.deep.equal({ key: 'value' });
    });
});

describe('Utils.expressAsyncWrapper', () => {
    it('should wrap async function and call it with arguments', async () => {
        const mockReq = { body: {} };
        const mockRes = { json: () => {} };
        const mockNext = () => {};
        
        let called = false;
        const asyncHandler = async (req, res, next) => {
            called = true;
            return 'success';
        };

        const wrapped = Utils.expressAsyncWrapper(asyncHandler);
        await wrapped(mockReq, mockRes, mockNext);

        expect(called).to.be.true;
    });

    it('should catch errors and pass to next', async () => {
        const mockReq = {};
        const mockRes = {};
        let errorPassed = null;
        const mockNext = (err) => { errorPassed = err; };

        const asyncHandler = async () => {
            throw new Error('Test error');
        };

        const wrapped = Utils.expressAsyncWrapper(asyncHandler);
        await wrapped(mockReq, mockRes, mockNext);

        expect(errorPassed).to.be.an.instanceof(Error);
        expect(errorPassed.message).to.equal('Test error');
    });
});
