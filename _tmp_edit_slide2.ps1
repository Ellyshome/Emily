$file = 'd:\app\Emily\docs\演示类\webPPT.html'
$content = Get-Content $file -Raw -Encoding UTF8

$oldBlock = @'
                            <div class="space-y-3 text-xs md:text-sm">
                                <div class="flex gap-2">
                                    <div class="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center text-xs font-bold">友</div>
                                    <div class="bg-slate-800 text-slate-200 p-3 rounded-2xl rounded-tl-none max-w-[80%]">
                                        "GXXXX到了，我该从哪个出口出来找你？"
                                    </div>
                                </div>
                                <div class="flex gap-2 flex-row-reverse">
                                    <div class="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center text-xs font-bold">我</div>
                                    <div class="bg-indigo-600 text-white p-3 rounded-2xl rounded-tr-none max-w-[80%]">
                                        等下，我查查通用AI助手...
                                    </div>
                                </div>
                            </div>
'@

$newBlock = @'
                            <div class="space-y-3 text-xs md:text-sm">
                                <!-- 我发的消息 -->
                                <div class="flex gap-2 flex-row-reverse">
                                    <div class="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center text-xs font-bold">我</div>
                                    <div class="bg-indigo-600 text-white p-3 rounded-2xl rounded-tr-none max-w-[80%]">
                                        "G12 到杭州东站在哪个出站口出？"
                                    </div>
                                </div>
                                <!-- 豆包回答 -->
                                <div class="flex gap-2">
                                    <div class="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-500 to-purple-500 flex items-center justify-center text-xs">
                                        <i class="fa-solid fa-bolt text-[10px] text-white"></i>
                                    </div>
                                    <div class="bg-slate-800 text-slate-200 p-3 rounded-2xl rounded-tl-none max-w-[85%] space-y-2">
                                        <p>G12次高铁通常到达杭州东站的<strong>东出站口</strong>或<strong>西出站口</strong>。</p>
                                        <p class="text-slate-400 text-xs">建议到站后查看现场指示牌，或咨询工作人员确认。</p>
                                    </div>
                                </div>
                            </div>
'@

if ($content.Contains($oldBlock)) {
    Write-Host "Found old block, replacing..."
    $content = $content.Replace($oldBlock, $newBlock)
    Set-Content -Path $file -Value $content -Encoding UTF8 -NoNewline
    Write-Host "Done."
} else {
    Write-Host "Old block NOT found."
    # Try to find the line numbers
    $lines = Get-Content $file -Encoding UTF8
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match 'space-y-3 text-xs md:text-sm') {
            Write-Host "Found at line $($i+1): $($lines[$i])"
        }
    }
}
