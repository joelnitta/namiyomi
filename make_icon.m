// Deterministic typography, using the system font without distributing it.
// clang -fno-modules -framework AppKit -framework CoreText make_icon.m -o /tmp/namiyomi-make-icon
// /tmp/namiyomi-make-icon assets
#import <AppKit/AppKit.h>
#import <CoreText/CoreText.h>

int main(int argc, const char **argv) { @autoreleasepool {
    NSString *output=argc>1 ? @(argv[1]) : @"assets";
    NSFileManager *fm=NSFileManager.defaultManager;
    [fm createDirectoryAtPath:output withIntermediateDirectories:YES attributes:nil error:NULL];
    NSBitmapImageRep *bitmap=[[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL pixelsWide:1024
        pixelsHigh:1024 bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO
        colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
    [NSGraphicsContext saveGraphicsState];
    NSGraphicsContext.currentContext=[NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
    CGContextRef c=NSGraphicsContext.currentContext.CGContext;
    CGContextClearRect(c,CGRectMake(0,0,1024,1024));
    CGContextSetRGBFillColor(c,245./255,241./255,231./255,1);
    CGPathRef tile=CGPathCreateWithRoundedRect(CGRectMake(32,32,960,960),196,196,NULL);
    CGContextAddPath(c,tile);CGContextFillPath(c);CGPathRelease(tile);
    CGContextSetRGBFillColor(c,52./255,52./255,50./255,1);
    CTFontRef font=CTFontCreateWithName(CFSTR("Arial-Black"),1000,NULL);
    const UniChar characters[]={'A','T','C','G'};
    for (int i=0;i<4;i++) {
        CGGlyph glyph;CTFontGetGlyphsForCharacters(font,&characters[i],&glyph,1);
        CGPathRef path=CTFontCreatePathForGlyph(font,glyph,NULL);
        CGRect bounds=CGPathGetPathBoundingBox(path);
        CGRect box=CGRectMake(i%2==0?104:536,i<2?534:106,384,384);
        CGAffineTransform t=CGAffineTransformMakeTranslation(box.origin.x,box.origin.y);
        t=CGAffineTransformScale(t,box.size.width/bounds.size.width,box.size.height/bounds.size.height);
        t=CGAffineTransformTranslate(t,-bounds.origin.x,-bounds.origin.y);
        CGPathRef fitted=CGPathCreateCopyByTransformingPath(path,&t);
        CGContextAddPath(c,fitted);CGContextFillPath(c);
        CGPathRelease(fitted);CGPathRelease(path);
    }
    CFRelease(font);[NSGraphicsContext restoreGraphicsState];
    [[bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}]
        writeToFile:[output stringByAppendingPathComponent:@"namiyomi.png"] atomically:YES];
    NSString *iconset=[output stringByAppendingPathComponent:@"namiyomi.iconset"];
    [fm createDirectoryAtPath:iconset withIntermediateDirectories:YES attributes:nil error:NULL];
    for (NSNumber *size in @[@16,@32,@128,@256,@512]) for (int scale=1;scale<=2;scale++) {
        NSInteger pixels=size.integerValue*scale;
        NSBitmapImageRep *rep=[[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL pixelsWide:pixels
            pixelsHigh:pixels bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES isPlanar:NO
            colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
        [NSGraphicsContext saveGraphicsState];
        NSGraphicsContext.currentContext=[NSGraphicsContext graphicsContextWithBitmapImageRep:rep];
        NSGraphicsContext.currentContext.imageInterpolation=NSImageInterpolationHigh;
        NSImage *image=[[NSImage alloc] initWithCGImage:bitmap.CGImage size:NSMakeSize(1024,1024)];
        [image drawInRect:NSMakeRect(0,0,pixels,pixels)];
        [NSGraphicsContext restoreGraphicsState];
        NSString *name=[NSString stringWithFormat:@"icon_%@x%@%@.png",size,size,scale==2?@"@2x":@""];
        [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}]
            writeToFile:[iconset stringByAppendingPathComponent:name] atomically:YES];
    }
    return 0;
}}
