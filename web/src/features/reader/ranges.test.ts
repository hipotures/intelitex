// @vitest-environment jsdom
import { it,expect } from 'vitest'
import { inlineRuns,utf16ToCodePoint,codePointToUtf16,selectedRange } from './ranges'
it('maps astral Unicode through inline elements to canonical code-point offsets',() => {
 document.body.innerHTML='<p data-block-id="b1">A😀<em>Żółw</em> rests.</p><p data-block-id="b2">Other</p>'
 const paragraph=document.querySelector('p')!; const em=document.querySelector('em')!
 const range=document.createRange();range.setStart(paragraph.firstChild!,1);range.setEnd(em.firstChild!,2)
 const selection=window.getSelection()!;selection.removeAllRanges();selection.addRange(range)
 expect(selectedRange(selection)).toEqual({block_id:'b1',start:1,end:4,text:'😀Żó'})
 range.setEnd(document.querySelectorAll('p')[1]!.firstChild!,2)
 expect(selectedRange(selection)).toBeNull()
 expect(utf16ToCodePoint('A😀B',3)).toBe(2);expect(codePointToUtf16('A😀B',2)).toBe(3)
})
it('rejects overlapping formatting and renders hostile text as text',() => {
 expect(inlineRuns('<script>😀</script>',[{start:0,end:2,style:'em'},{start:1,end:3,style:'strong'}])).toEqual([{text:'<script>😀</script>',style:null}])
 expect(inlineRuns('A😀B',[{start:1,end:2,style:'em'}])).toEqual([{text:'A',style:null},{text:'😀',style:'em'},{text:'B',style:null}])
})
