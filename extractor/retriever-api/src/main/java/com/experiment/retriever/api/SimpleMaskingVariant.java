package com.experiment.retriever.api;

import java.util.Collection;
import java.util.List;

/**
 * Simple immutable implementation of {@link IMaskingVariant}.
 * Provides a factory method to construct from body start/end offsets
 * as stored in extracted method data.
 */
public record SimpleMaskingVariant(
        int offsetInMaskedSource,
        MaskingRange maskingRange,
        Collection<MaskingRange> extraMaskingOffsets,
        boolean emptyMaskedPart
) implements IMaskingVariant {

    @Override
    public int getOffsetInMaskedSource() {
        return offsetInMaskedSource;
    }

    @Override
    public MaskingRange getMaskingRange() {
        return maskingRange;
    }

    @Override
    public Collection<MaskingRange> getExtraMaskingOffsets() {
        return extraMaskingOffsets;
    }

    @Override
    public boolean isEmptyMaskedPart() {
        return emptyMaskedPart;
    }

    /**
     * Constructs a masking variant from body offsets as stored in extraction data.
     *
     * <p>The body start offset points to the opening '{' of the method body.
     * The actual masked region starts at bodyStartOffset + 1 (inside the brace)
     * and ends at bodyEndOffset (the closing '}').
     *
     * @param fileContent      full source file text
     * @param bodyStartOffset  offset of the '{' opening the method body
     * @param bodyEndOffset    offset of the '}' closing the method body
     * @return a masking variant representing the method body region
     */
    public static SimpleMaskingVariant fromBodyOffsets(
            String fileContent, int bodyStartOffset, int bodyEndOffset) {
        int maskStart = bodyStartOffset + 1;
        int maskEnd = bodyEndOffset;
        MaskingRange range = new MaskingRange(maskStart, maskEnd);

        boolean empty = fileContent.substring(maskStart, maskEnd).isBlank();

        return new SimpleMaskingVariant(maskStart, range, List.of(), empty);
    }
}
