import { formatTimecode } from "@/lib/types";

describe("formatTimecode", () => {
  it("formats zero", () => {
    expect(formatTimecode(0)).toBe("00:00:00");
  });

  it("formats minutes and seconds", () => {
    expect(formatTimecode(65_500)).toBe("00:01:05");
  });

  it("formats hours", () => {
    expect(formatTimecode(3_723_004)).toBe("01:02:03");
  });

  it("clamps negative values to zero", () => {
    expect(formatTimecode(-100)).toBe("00:00:00");
  });
});
