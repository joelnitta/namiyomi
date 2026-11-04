"""Place real AB1 signal samples on aligned columns without breaking the trace.

Alignment gaps stretch the horizontal coordinate between neighboring real peaks;
they do not remove samples or synthesize intensities. Gap base calls are rendered
as '-' by the viewer. Outside source-read coverage there is no signal to draw.
"""
import bisect
import math


def trace_samples(trace,mapping,left,right,cell,reverse=False):
    anchors=[(i,float(trace.peaks[b.raw]),b.raw) for i,b in enumerate(mapping)
             if b is not None and b.raw is not None]
    if not anchors or right<=left:return []
    direction=-1 if reverse else 1
    first_col,first_peak,first_raw=anchors[0]
    last_col,last_peak,last_raw=anchors[-1]
    def boundary(raw,side):
        neighbor=raw+side*direction
        peak=float(trace.peaks[raw])
        if 0<=neighbor<len(trace.peaks):return (peak+trace.peaks[neighbor])/2
        other=raw-side*direction
        spacing=abs(peak-trace.peaks[other]) if 0<=other<len(trace.peaks) else 12
        return peak+side*direction*max(1,spacing)/2
    knots=[(first_col-.5,boundary(first_raw,-1))]
    knots.extend((col,peak) for col,peak,raw in anchors)
    knots.append((last_col+.5,boundary(last_raw,1)))
    columns=[col for col,peak in knots]
    begin=max(0,bisect.bisect_right(columns,left-.5)-1)
    end=min(len(knots)-1,bisect.bisect_left(columns,right-.5)+1)
    limit=min(map(len,trace.channels.values()))
    output=[]
    for (c0,p0),(c1,p1) in zip(knots[begin:end],knots[begin+1:end+1]):
        if p0==p1:continue
        lo=max(0,math.ceil(min(p0,p1)));hi=min(limit-1,math.floor(max(p0,p1)))
        indices=range(lo,hi+1) if p1>p0 else range(hi,lo-1,-1)
        for sample in indices:
            col=c0+(sample-p0)/(p1-p0)*(c1-c0)
            if not left-.5<=col<=right-.5:continue
            point=(82+(col-left)*cell,sample)
            if not output or output[-1]!=point:output.append(point)
    return output
