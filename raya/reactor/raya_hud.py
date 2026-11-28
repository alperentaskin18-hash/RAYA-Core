"""R.A.Y.A REACTOR — reference-matched animated desktop prototype.
Python 3.11+, Pillow; no model calls, no microphone or network access.
Default: reference-matched skin composed from independent reference artwork
plates; interactive Python layers and animated reactor. --procedural draws
the entire screen including the reactor from code. --hybrid redraws the UI
with the original reactor artwork. No screenshot is passed off as code-only.
All telemetry and tasks are DEMO examples. F2 toggles rendering; F3 compares
with the supplied original; F11 fullscreen; Space pauses outside command input.
"""
from __future__ import annotations
import math, os, time
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, ImageEnhance

W,H=1648,928
HERE=Path(__file__).resolve().parent
TAU=math.tau
CY='#05e8ff'; ICE='#a8eeff'; WHITE='#e4f1fa'; MUT='#8aaec5'; BLUE='#117ca9'; GREEN='#00ed65'; AMBER='#ffba50'; BG='#020e19'
@lru_cache(None)
def font(size,bold=False):
    paths=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/('segoeuib.ttf' if bold else 'segoeui.ttf'),Path('/usr/share/fonts/truetype/lato')/('Lato-Bold.ttf' if bold else 'Lato-Regular.ttf'),Path('/usr/share/fonts/truetype/dejavu')/('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')]
    for p in paths:
        if p.exists(): return ImageFont.truetype(str(p),size)
    return ImageFont.load_default(size=size)
def txt(d,x,y,s,z=16,c=WHITE,b=False,a='lm'):
    d.text((x,y),str(s),font=font(z,b),fill=c,anchor=a)
def line(d,pts,c=CY,w=1):d.line(pts,fill=c,width=w,joint='curve')
def circ(d,x,y,r,c,w=1):d.ellipse((x-r,y-r,x+r,y+r),outline=c,width=w)
def dot(d,x,y,r,c):d.ellipse((x-r,y-r,x+r,y+r),fill=c)
def arc(d,x,y,r,a,b,c,w=1):d.arc((x-r,y-r,x+r,y+r),a,b,fill=c,width=w)
def rr(d,box,r=10,fill=None,outline=None,w=1):d.rounded_rectangle(box,r,fill=fill,outline=outline,width=w)
def progress(d,x,y,w,v,h=7):
    rr(d,(x,y,x+w,y+h),h//2,'#113348');rr(d,(x,y,x+max(2,w*v),y+h),h//2,CY)

def icon(d,name,x,y,size=22,c=CY):
    # All icons are vector strokes; no emoji/font icons or downloaded assets.
    s=size/24; w=max(1,round(s*1.8))
    def p(a,b):return (x+a*s,y+b*s)
    def l(*pts):line(d,[p(*v) for v in pts],c,w)
    def e(a,b,r):circ(d,*p(a,b),r*s,c,w)
    def box(a,b,cc,dd,r=2):rr(d,(*p(a,b),*p(cc,dd)),r*s,None,c,w)
    if name=='home':l((-11,-1),(0,-11),(11,-1));l((-8,-3),(-8,10),(8,10),(8,-3));l((-3,10),(-3,2),(3,2),(3,10))
    elif name in ('layers','quota'):
        for off in [-5,1,7]:l((-10,off),(0,off-5),(10,off),(0,off+5),(-10,off))
    elif name in ('task','checklist'):
        box(-9,-9,9,11);l((-4,-10),(-4,-13),(4,-13),(4,-10));l((-5,0),(-1,4),(5,-3))
    elif name=='grid':
        for a in [-10,2]:
            for b in [-10,2]:box(a,b,a+8,b+8)
    elif name=='flow':
        l((0,-5),(0,1),(-8,7));l((0,1),(8,7));e(0,-9,4);e(-8,9,3);e(8,9,3)
    elif name in ('shield','secure'):
        l((0,-11),(10,-7),(8,4),(0,11),(-8,4),(-10,-7),(0,-11));l((-4,0),(-1,4),(5,-3))
    elif name=='gear':
        e(0,0,8);e(0,0,3)
        for i in range(8):a=i*TAU/8;l((8*math.cos(a),8*math.sin(a)),(11*math.cos(a),11*math.sin(a)))
    elif name=='bell':l((-9,6),(-6,3),(-6,-5),(-3,-9),(3,-9),(6,-5),(6,3),(9,6),(-9,6));l((-3,9),(0,11),(3,9))
    elif name=='activity':l((-12,0),(-7,0),(-4,-10),(0,9),(3,-5),(6,0),(12,0))
    elif name in ('cpu','ram','gpu'):
        box(-8,-8,8,8);box(-4,-4,4,4,0)
        for a in [-6,-2,2,6]:l((a,-11),(a,-8));l((a,8),(a,11));l((-11,a),(-8,a));l((8,a),(11,a))
        if name=='gpu':txt(d,x,y,'GPU',max(6,int(size*.29)),c,True,'mm')
    elif name=='wifi':
        for r in [11,7,3]:d.arc((*p(-r,-r),*p(r,r)),210,330,fill=c,width=w)
        dot(d,x,y+4*s,1.6*s,c)
    elif name=='infinity':
        l(*[(10*math.sin(i*TAU/80),5*math.sin(2*i*TAU/80)) for i in range(81)])
    elif name=='spark':d.polygon([p(0,-12),p(3,-3),p(12,0),p(3,3),p(0,12),p(-3,3),p(-12,0),p(-3,-3)],fill=c)
    elif name=='cube':l((0,-11),(10,-5),(10,6),(0,12),(-10,6),(-10,-5),(0,-11));l((-10,-5),(0,1),(10,-5));l((0,1),(0,12))
    elif name=='expand':l((-10,-4),(-10,-10),(-4,-10));l((4,-10),(10,-10),(10,-4));l((-10,4),(-10,10),(-4,10));l((4,10),(10,10),(10,4));l((3,-3),(10,-10));l((-3,3),(-10,10))
    elif name=='plus':box(-11,-11,11,11);l((-6,0),(6,0));l((0,-6),(0,6))
    elif name=='code':l((-5,-6),(-11,0),(-5,6));l((5,-6),(11,0),(5,6));l((3,-10),(-3,10))
    elif name=='database':
        d.ellipse((*p(-9,-10),*p(9,-3)),outline=c,width=w);l((-9,-6),(-9,8));l((9,-6),(9,8))
        for q in [1,7]:d.arc((*p(-9,q-4),*p(9,q+4)),0,180,fill=c,width=w)
    elif name=='play':e(0,0,11);d.polygon([p(-3,-6),p(7,0),p(-3,6)],fill=c)
    elif name=='check':dot(d,x,y,10*s,c);line(d,[p(-5,0),p(-1,4),p(5,-4)],'#002423',w)
    elif name=='info':e(0,0,10);l((0,-1),(0,5));dot(d,x,y-5*s,1.3*s,c)
    elif name=='key':e(-5,-4,5);l((-2,0),(9,10),(11,8),(8,5));l((5,6),(8,3))
    elif name=='lock':box(-8,-1,8,10);d.arc((*p(-5,-11),*p(5,3)),180,360,fill=c,width=w);l((0,3),(0,6))
    elif name=='mic':box(-4,-11,4,4,4);d.arc((*p(-8,-8),*p(8,9)),0,180,fill=c,width=w);l((0,9),(0,13));l((-5,13),(5,13))
    elif name=='paperclip':l((5,-8),(5,6),(2,10),(-2,10),(-5,7),(-5,-8),(-2,-11),(2,-11),(3,-8),(3,6),(0,7),(-1,5),(-1,-5))
    elif name=='send':d.polygon([p(-10,-11),p(12,0),p(-10,11),p(-4,1)],fill=c);l((-5,0),(8,0))
    elif name=='terminal':l((-10,-6),(-3,0),(-10,6));l((1,8),(10,8))
    elif name=='bolt':d.polygon([p(0,-12),p(-10,2),p(-2,2),p(-5,12),p(10,-3),p(2,-3)],fill=c)
    elif name=='network':
        for i in range(6):a=i*TAU/6;b=(i+1)*TAU/6;l((10*math.cos(a),10*math.sin(a)),(10*math.cos(b),10*math.sin(b)));e(10*math.cos(a),10*math.sin(a),2);l((0,0),(10*math.cos(a),10*math.sin(a)))
    elif name=='logo':l((-13,-10),(13,-10),(0,14),(-13,-10));l((-13,-10),(0,-3),(13,-10));l((0,-3),(0,14));e(0,1,4)
    else:e(0,0,8)

# Layout is intentionally in the reference's 1648×928 design coordinate space.
PANELS=[(21,89,496,344,'SİSTEM KAYNAKLARI','activity'),(21,358,496,564,'AI SAĞLAYICILAR','layers'),(21,576,496,743,'KOTALAR & KULLANIM','quota'),(1116,89,1632,339,'GÖREV YÖNETİCİSİ','task'),(1116,353,1632,540,'AKTİVİTE ZAMAN ÇİZELGESİ','activity'),(1116,554,1367,743,'GÜVENLİK DURUMU','shield'),(1379,554,1632,743,'BİLDİRİMLER','bell')]
NAV=[('Ana Sayfa','home',369,505),('Yetenekler','layers',516,655),('Görevler','task',659,782),('AI Merkezi','grid',787,922),('İş Akışı','flow',928,1042),('Güvenlik','shield',1048,1165),('Ayarlar','gear',1171,1286)]

def panel(im,box,title,ic):
    x,y,r,b=box;w=r-x;h=b-y
    plate=Image.new('RGB',(w+1,h+1));d=ImageDraw.Draw(plate)
    for j in range(h+1):
        k=math.exp(-j/45);k2=math.exp(-(h-j)/24)
        d.line((0,j,w,j),fill=(3,int(19+12*k+5*k2),int(30+18*k+9*k2)))
    mask=Image.new('L',plate.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,w,h),17,fill=255)
    im.paste(plate,(x,y),mask);d=ImageDraw.Draw(im)
    rr(d,box,17,None,'#058ab9');rr(d,(x+1,y+1,r-1,b-1),16,None,'#16445e')
    line(d,[(x+18,y+1),(x+w*.72,y+1)],'#2ac9e9');line(d,[(x+18,y+43),(r-10,y+43)],'#17465e')
    icon(d,ic,x+34,y+23,26);txt(d,x+58,y+23,title,19 if w>300 else 14,WHITE,True)
    icon(d,'plus' if ic=='layers' else 'expand',r-29,y+23,19,MUT if ic!='layers' else ICE)

def add_glow(im,paths):
    layer=Image.new('RGB',im.size);d=ImageDraw.Draw(layer)
    for pts,c,w in paths:line(d,pts,c,w)
    for radius,weight in [(12,.52),(4,.55),(1,.9)]:
        im=ImageChops.add(im,ImageEnhance.Brightness(layer.filter(ImageFilter.GaussianBlur(radius))).enhance(weight))
    return im

@lru_cache(None)
def base():
    im=Image.new('RGB',(W,H));d=ImageDraw.Draw(im)
    for y in range(H):
        k=math.exp(-((y-420)/320)**2);d.line((0,y,W,y),fill=(2,int(11+7*k),int(21+14*k)))
    # dark technical frame and diagonal detail lines
    for x in range(0,W,41):line(d,[(x,72),(x,756)],'#061d2a')
    for y in range(75,760,41):line(d,[(0,y),(W,y)],'#061a29')
    for k in range(11):
        line(d,[(500+k*18,77),(824,390),(1115-k*18,77)],'#092638')
        line(d,[(500+k*18,757),(824,390),(1115-k*18,757)],'#082333')
    # Header and custom chrome
    d.polygon([(0,0),(366,0),(317,64),(297,71),(0,71)],fill='#041a29');line(d,[(0,71),(297,71),(317,64),(366,0)],'#17bbeb')
    d.polygon([(335,0),(1366,0),(1309,61),(355,61),(330,49)],fill='#061a28');line(d,[(351,61),(1308,61),(1366,0)],'#1ab6db')
    icon(d,'logo',74,32,51);txt(d,135,28,'R.A.Y.A',32,WHITE,True);txt(d,137,52,'AI ORCHESTRATOR',14,ICE)
    for n,ic,x,r in NAV:
        if n=='Ana Sayfa':rr(d,(x,10,r,57),10,'#08405c','#0d5c81');line(d,[(x+12,56),(r-11,56)],CY,2)
        icon(d,ic,x+31,33,23,CY if n=='Ana Sayfa' else ICE);txt(d,x+52,33,n,15,ICE if n=='Ana Sayfa' else WHITE)
    rr(d,(1372,19,1493,56),12,'#00251d','#00673b');dot(d,1394,37,5,GREEN);txt(d,1408,37,'Çevrimiçi',15,GREEN)
    rr(d,(1516,19,1566,57),9,'#061c2c','#164963');icon(d,'bell',1540,39,25,ICE);dot(d,1555,27,4,AMBER)
    for i,(s,x) in enumerate([('−',1590),('□',1610),('×',1629)]):txt(d,x,16,s,14,MUT,False,'mm')
    for x,y,r,b,title,ic in PANELS:panel(im,(x,y,r,b),title,ic)
    d=ImageDraw.Draw(im)
    # Panel separators, fixed text, provider badges
    for i,y in enumerate([164,212,260,314]):
        icon(d,['cpu','gpu','ram','wifi'][i],59,y,25,ICE);txt(d,98,y,['CPU','GPU','RAM','Ağ'][i],16,WHITE)
        if i<3:line(d,[(45,y+25),(477,y+25)],'#103044')
    for i,(name,ic) in enumerate([('OmniRoute','infinity'),('Gemini','spark'),('Yerel (Ollama)','cube')]):
        y=430+i*49;rr(d,(39,y-19,84,y+19),9,'#082b43');icon(d,ic,61,y,29,'#71d5ff' if i!=1 else '#89a6ff');txt(d,95,y,name,16,WHITE,True);dot(d,233,y,5,GREEN);txt(d,246,y,'Aktif',15,GREEN);[dot(d,472,y+dy,1.5,ICE) for dy in (-5,0,5)]
        if i<2:line(d,[(40,y+24),(479,y+24)],'#14394d')
    txt(d,208,646,'Aylık Limit',15,WHITE);txt(d,208,681,'Kullanılan',15,WHITE);txt(d,208,706,'Kalan',15,WHITE)
    txt(d,386,646,'150.000',17,WHITE,True,'rm');txt(d,386,681,'31.642',17,WHITE,False,'rm');txt(d,386,706,'118.358',17,WHITE,False,'rm')
    # Right column, activity and security
    for i,(t,c,tm) in enumerate([('OmniRoute bağlantısı doğrulandı.',GREEN,'14:23'),('Gemini model kataloğu alındı (27 model).',GREEN,'14:17'),('AI Control Center yapılandırması güncellendi.',AMBER,'14:09'),('Sistem kaynakları senkronize edildi.',GREEN,'14:02'),('R.A.Y.A başlatıldı.',CY,'13:58')]):
        y=413+i*27;line(d,[(1135,y),(1148,y)],c);icon(d,'gear' if i==2 else 'info' if i==4 else 'check',1159,y,17,c);txt(d,1191,y,t,14,WHITE);txt(d,1614,y,tm,14,MUT,False,'rm');line(d,[(1133,y+13),(1616,y+13)],'#103245')
    for i,(t,ic,v) in enumerate([('Sistem Bütünlüğü','secure','Tam'),('API Anahtarları','key','Güvenli'),('İzinler','lock','Doğrulandı'),('Sandbox','layers','Aktif')]):
        y=618+i*30;icon(d,ic,1150,y,20,GREEN if i<2 else CY);txt(d,1170,y,t,14,WHITE);txt(d,1344,y,v,14,GREEN,False,'rm')
    for i,(t,c,tm) in enumerate([('HUD modülü yüklendi.',GREEN,'2 dk önce'),('Yeni model sürümleri var.',AMBER,'14 dk önce'),('Kota kullanımı %20’yi geçti.',CY,'27 dk önce')]):
        y=625+i*42;dot(d,1400,y,5,c);txt(d,1413,y,t,12,WHITE);txt(d,1613,y,tm,11,MUT,False,'rm')
    # Center title and status tiles
    txt(d,824,100,'R . A . Y . A',35,WHITE,True,'mm');txt(d,824,130,'K O N T R O L   M E R K E Z İ',13,'#65b9d8',False,'mm')
    for x in [590,950]:line(d,[(x,130),(x+72,130)],'#11a9d2')
    for x,r,ic,t,v in [(543,709,'bolt','Aktif Mod','Otonom'),(732,900,'network','Çalışan İş Akışı','3'),(926,1095,'layers','Aktif Yetenek','12')]:
        rr(d,(x,669,r,732),15,'#061b2c','#118db6');line(d,[(x+15,730),(r-15,730)],'#2dd5ee');icon(d,ic,x+32,700,37);txt(d,x+65,688,t,13,MUT);txt(d,x+65,712,v,17,WHITE)
    # Full-width command dock and custom footer frame
    d.polygon([(60,758),(1579,758),(1610,790),(1610,837),(1577,852),(50,852),(21,834),(21,796)],fill='#031521');line(d,[(60,758),(1579,758),(1610,790),(1610,837)],'#145675')
    rr(d,(124,779,1132,834),14,'#041e31','#0ce1f5');icon(d,'terminal',162,807,26,ICE);icon(d,'paperclip',1101,807,23,ICE)
    rr(d,(1438,779,1517,833),14,'#084761','#14d7ff');icon(d,'send',1479,807,31,CY)
    d.polygon([(0,896),(42,854),(752,854),(788,878),(869,878),(907,854),(1598,854),(1648,890),(1648,928),(0,928)],fill='#041724');line(d,[(0,896),(42,854),(752,854),(788,878),(869,878),(907,854),(1598,854),(1648,890)],'#1fc2ed')
    icon(d,'lock',72,886,20,ICE);txt(d,104,886,'Sistem:',14,MUT);txt(d,163,886,'Hazır',14,GREEN,True);txt(d,220,886,'Mod: Otonom',14,WHITE);txt(d,381,886,'Bağlantılar: 2 Aktif',14,WHITE);txt(d,556,886,'Kuyruk: 3 Görev',14,WHITE)
    for x in [205,360,540]:line(d,[(x,875),(x,899)],'#1a5f7c')
    for x,r,ic in [(944,1065,'cpu'),(1076,1190,'gpu'),(1201,1313,'ram'),(1326,1625,'wifi')]:rr(d,(x,868,r,904),12,'#061c2d','#1a4d65');icon(d,ic,x+24,886,20,ICE)
    return add_glow(im,[([(0,70),(293,70),(318,56)],'#059bcb',2), ([(389,55),(484,55)],'#08d6ff',3), ([(41,89),(475,89)],'#068bb8',2), ([(41,343),(475,343)],'#088fba',2), ([(41,563),(475,563)],'#079fc8',2), ([(1134,338),(1615,338)],'#079fc8',2), ([(1136,742),(1345,742)],'#079fc8',2), ([(1397,742),(1615,742)],'#079fc8',2), ([(124,801),(124,814),(145,834),(1112,834)],'#09b5ed',2), ([(48,854),(749,854)],'#0f82b4',2)])

# Static outer concentric rings; independent moving plasma and elliptical orbit paths.
# t is measured in units of the original 8-second animation cycle.
CORE_PERIOD_SECONDS = 80.0
ORBIT_PERIOD_SECONDS = 12.0
CYCLE_SECONDS = 8.0
@lru_cache(None)
def artwork():
    p=HERE/'reactor_clean.png'
    return Image.open(p).convert('RGB') if p.exists() else None
@lru_cache(None)
def radial_mask(w,h,cx,cy,lo,hi,fade=8):
    m=Image.new('L',(w,h));d=ImageDraw.Draw(m);d.ellipse((cx-hi,cy-hi,cx+hi,cy+hi),fill=255)
    if lo>0:d.ellipse((cx-lo,cy-lo,cx+lo,cy+lo),fill=0)
    return m.filter(ImageFilter.GaussianBlur(fade))

# Fixed, coplanar Saturn-like projected orbits. Only phase advances.
# Screen y points down, so the near half (sin(a) > 0) travels right to left.
ORBIT_TILT = math.radians(-21)

def orbit_specs(reference=True):
    return ((148, 43), (137, 32)) if reference else ((148, 43), (137, 32), (126, 24))

def orbit_point(cx, cy, rx, ry, a):
    co, si = math.cos(ORBIT_TILT), math.sin(ORBIT_TILT)
    u, v = rx*math.cos(a), ry*math.sin(a)
    return cx+u*co-v*si, cy+u*si+v*co

def orbit_visibility(a, x, y, cx, cy):
    # Attenuate the rear half further where it passes behind the plasma sphere.
    near = (1+math.sin(a))/2
    brightness = .34+.66*near
    if math.sin(a)<0:
        r = math.hypot(x-cx,y-cy)
        brightness *= .18+.82*max(0,min(1,(r-79)/19))
    return brightness

def orbit_color(color, brightness):
    return tuple(round(max(0,min(255,v*brightness))) for v in color)

@lru_cache(maxsize=8)
def static_orbits(size, cx, cy, reference=True):
    """A time-independent luminous pair of ellipses, with depth shading."""
    layer=Image.new('RGB',size);d=ImageDraw.Draw(layer)
    for j,(rx,ry) in enumerate(orbit_specs(reference)):
        for i in range(360):
            a=i*TAU/360; b=(i+1)*TAU/360
            x,y=orbit_point(cx,cy,rx,ry,(a+b)/2)
            v=orbit_visibility((a+b)/2,x,y,cx,cy)
            line(d,[orbit_point(cx,cy,rx,ry,a),orbit_point(cx,cy,rx,ry,b)],
                 orbit_color((112,220,249) if j==0 else (67,178,224),v),2 if j==0 else 1)
    return ImageChops.add(layer,ImageEnhance.Brightness(layer.filter(ImageFilter.GaussianBlur(4))).enhance(.85))

def draw_orbits(t, size, cx, cy, reference=True):
    """Fixed planes and radii; moving lights only, never rotate/flip the paths."""
    layer=Image.new('RGB',size);d=ImageDraw.Draw(layer)
    phase=t*TAU*CYCLE_SECONDS/ORBIT_PERIOD_SECONDS
    for j,(rx,ry) in enumerate(orbit_specs(reference)):
        # Same direction on every ring. Near side moves right to left.
        for k in range(3 if j==0 else 2):
            a=phase+j*.85+k*TAU/(3 if j==0 else 2)+.45
            # A short glowing tail follows the very same fixed elliptical path.
            for step in range(12,0,-1):
                q=a-step*.022; q2=q+.022
                x,y=orbit_point(cx,cy,rx,ry,q)
                v=orbit_visibility(q,x,y,cx,cy)*(1-step/13)**1.7
                line(d,[(x,y),orbit_point(cx,cy,rx,ry,q2)],orbit_color((80,224,255),v),2)
            x,y=orbit_point(cx,cy,rx,ry,a)
            v=orbit_visibility(a,x,y,cx,cy)
            r=(4.6 if j==0 else 3.3)*(.65+.35*v)
            dot(d,x,y,r,orbit_color((198,250,255),v))
            dot(d,x,y,max(1,r*.48),orbit_color((255,255,255),v))
    glow=ImageEnhance.Brightness(layer.filter(ImageFilter.GaussianBlur(6))).enhance(1.15)
    particles=ImageChops.add(ImageChops.add(layer,glow),layer.filter(ImageFilter.GaussianBlur(2)))
    return ImageChops.add(static_orbits(size,cx,cy,reference),particles)

def art_core(t):
    original=artwork();im=original.copy();w,h=im.size;cx,cy=326,251
    # Rotate only the inner plasma texture, never the circular HUD bands.
    rotated=original.rotate(-360*t*CYCLE_SECONDS/CORE_PERIOD_SECONDS,Image.Resampling.BICUBIC,center=(cx,cy))
    im.paste(rotated,(0,0),radial_mask(w,h,cx,cy,0,87,3))
    return ImageChops.add(im,draw_orbits(t,im.size,cx,cy))

def proc_core(t):
    w,h=617,528;cx,cy=326,251;p=t*TAU*CYCLE_SECONDS/CORE_PERIOD_SECONDS
    im=Image.new('RGB',(w,h),'#031420');d=ImageDraw.Draw(im)
    # concentric radial ambience and projection stage
    for r in range(285,1,-2):
        z=math.exp(-((r-112)/100)**2);dot(d,cx,cy,r,(2,int(13+14*z),int(26+35*z)))
    for i in range(24):
        a=i*TAU/24;line(d,[(cx+155*math.cos(a),cy+155*math.sin(a)),(cx+305*math.cos(a),cy+305*math.sin(a))],'#104362')
    def pt(r,a):return (cx+r*math.cos(a),cy+r*math.sin(a))
    for r in (91,105,125,145,170,193,214,229,249):circ(d,cx,cy,r,'#185a83')
    for i in range(240):
        a=i*TAU/240;line(d,[pt(238 if i%5==0 else 244,a),pt(249,a)],'#4c9ec2' if i%5==0 else '#123c59')
    layer=Image.new('RGB',(w,h));q=ImageDraw.Draw(layer)
    for r,offset,n,span,c,ww in [(216,0,8,29,'#0879b6',14),(196,0,7,28,'#00dbff',9),(174,0,6,23,'#0c85c2',5),(150,0,9,25,'#148fc5',4),(129,0,12,19,'#10caff',2),(212,34,3,22,AMBER,8)]:
        for i in range(n):a=offset+i*360/n;arc(q,cx,cy,r,a,a+span,c,ww)
    for i in range(12):a=i*TAU/12;line(q,[pt(254,a),pt(266,a)],'#0de5ff',2)
    # plasma sphere with interweaving filaments, longitude/latitude curves, orbital paths
    for r in range(98,1,-1):
        z=math.exp(-((r-60)/38)**2);dot(d,cx,cy,r,(0,int(22+40*z),int(55+85*z)))
    for j in range(18):
        a=j*TAU/18+p*.5
        pts=[pt(4+k*1.85,a+.4*math.sin(k*.14+p+j)+.3*math.sin(k*.37+j)) for k in range(50)]
        line(q,pts,'#1b9dff',1+(j%3==0))
    circ(q,cx,cy,94,'#61eaff',3);circ(q,cx,cy,99,'#008dff',2)
    for r in range(34,0,-1):z=(1-r/35)**.45;dot(q,cx,cy,r,(int(50+205*z),int(170+85*z),255))
    for j in range(28):a=j*TAU/28; x,y=pt(96+23*math.sin(p+j),a);dot(q,x,y,1.5,'#5bdfff')
    for r,c,ww in [(250,'#d38d2c',2),(218,'#127db7',2),(190,'#00d7ff',4),(142,'#25a5e6',3),(96,'#a1f7ff',2),(52,'#00c7ff',3)]:q.ellipse((cx-r,485-r*.125,cx+r,485+r*.125),outline=c,width=ww)
    line(q,[(cx,cy+93),(cx,485)],'#06c4ff',3);line(q,[(cx-30,485),(cx,cy+93),(cx+30,485)],'#086da5',1)
    for blur,weight in [(18,.85),(7,.75),(2,.8),(0,1)]:
        l=layer.filter(ImageFilter.GaussianBlur(blur)) if blur else layer;im=ImageChops.add(im,ImageEnhance.Brightness(l).enhance(weight))
    return ImageChops.add(im,draw_orbits(t,im.size,cx,cy,False))

TASKS=['R.A.Y.A HUD geliştirmesi','Model entegrasyon testi','Skill kütüphanesi güncelleme']

def frame(t=0.0,procedural=False,command='',tab=0,mic=False,selected='Ana Sayfa',notice='',focus=False):
    im=base().copy();p=t*TAU;d=ImageDraw.Draw(im)
    core=proc_core(t) if procedural or artwork() is None else art_core(t)
    im.paste(core,(498,140));d=ImageDraw.Draw(im)
    # System metrics: example values at phase 0; bounded periodic demo movement.
    for i,(v,y) in enumerate(zip([.34,.47,.59],[164,212,260])):
        k=max(0,min(1,v+.025*math.sin(p+i)*math.sin(p)));txt(d,374,y-1,f'%{round(k*100)}',16,WHITE,False,'rm');progress(d,164,y+11,210,k)
        if i==2:txt(d,164,y,'9.4 / 16.0 GB',16,WHITE)
    txt(d,174,314,'↓ 124.6 Mbps',15,ICE);txt(d,311,314,'↑ 36.8 Mbps',15,ICE)
    for i,y in enumerate([164,212,260,314]):
        pts=[(403+j*3.6,y+10+7*math.sin(j*.83+p+i)+4*math.sin(j*1.83-p)) for j in range(21)]
        d.polygon(pts+[(475,y+20),(403,y+20)],fill='#06384d');line(d,pts,CY,1)
    for i,(v,s) in enumerate([(.124,'12.4K / 100K'),(.174,'8.7K / 50K')]):y=430+i*49;txt(d,307,y-7,s,15,WHITE);progress(d,307,y+10,144,v)
    txt(d,312,528,'—',16,ICE);circ(d,107,681,50,'#123d54',11);arc(d,107,681,50,-90,-14,CY,11);txt(d,107,669,'%21',20,CY,True,'mm');txt(d,107,692,'Toplam',13,WHITE,False,'mm');txt(d,107,707,'Kullanım',13,WHITE,False,'mm')
    for i,h in enumerate([13,25,18,40,32,60]):d.rectangle((417+i*10,715-h,422+i*10,715),fill='#12b9e0' if i%2==0 else '#086c92')
    for i,(s,x,r) in enumerate([('Aktif (3)',1132,1236),('Bekleyen (2)',1243,1366),('Tamamlanan (128)',1373,1530)]):rr(d,(x,139,r,172),11,'#08618a' if tab==i else '#082737','#0e5c78' if tab==i else None);txt(d,(x+r)/2,155,s,14,WHITE,False,'mm')
    if tab==0:
        for i,(s,v,ic) in enumerate(zip(TASKS,[.68,.42,.27],['code','database','gear'])):
            y=198+i*52;icon(d,ic,1157,y+6,25);txt(d,1198,y,s,15,WHITE);txt(d,1561,y,f'%{round(v*100)}',15,WHITE,False,'rm');progress(d,1198,y+15,369,v);icon(d,'play',1595,y+3,33,'#60dcff')
    else:
        vals=['Gemini maliyet doğrulaması','Windows ses testi'] if tab==1 else ['OmniRoute model keşfi','Gemini katalog bağlantısı','HUD kurulumu']
        for i,s in enumerate(vals):y=199+i*48;icon(d,'info' if tab==1 else 'check',1157,y,22,AMBER if tab==1 else GREEN);txt(d,1198,y,s,15);txt(d,1608,y,'Bekliyor' if tab==1 else 'Tamamlandı',13,MUT,False,'rm')
    # Glowing microphone ring and animated waveform (decorative demo, not live audio).
    layer=Image.new('RGB',(W,H));q=ImageDraw.Draw(layer);circ(q,1283,806,34,'#11d8ff',3);circ(q,1283,806,29,'#075c80',3);icon(q,'mic',1283,805,32,'#21edff')
    for x in [1175,1186,1198,1210,1222,1343,1355,1367,1379,1390]:
        z=5+13*abs(math.sin(p*3+x*.043))*(1 if not mic else 1.3);line(q,[(x,806-z),(x,806+z)],CY,4)
    im=ImageChops.add(im,layer.filter(ImageFilter.GaussianBlur(7)));im=ImageChops.add(im,layer);d=ImageDraw.Draw(im)
    txt(d,200,807,command[-92:] if command else 'R.A.Y.A’ya bir komut yazın…',18,WHITE if command else '#5f8da7');
    if focus and int(t*12)%2==0:
        length=d.textlength(command[-92:],font=font(18));line(d,[(200+length,794),(200+length,820)],CY,1)
    for x,s in [(986,'CPU %34'),(1116,'GPU %47'),(1241,'RAM %59'),(1370,'↓ 124.6 Mbps ↑ 36.8 Mbps')]:txt(d,x,886,s,14,WHITE)
    if selected!='Ana Sayfa':
        rr(d,(369,10,505,57),10,'#061a28');icon(d,'home',400,33,23,ICE);txt(d,421,33,'Ana Sayfa',15,WHITE)
        for n,ic,x,r in NAV:
            if n==selected:rr(d,(x,10,r,57),10,'#08405c','#11c9ef');icon(d,ic,x+31,33,23);txt(d,x+52,33,n,15,WHITE)
    if notice:
        rr(d,(420,748,1230,774),9,'#0a2d43','#18a9d1');txt(d,825,761,notice[:104],14,ICE,False,'mm')
    return im

@lru_cache(None)
def reference_plates():
    p=HERE/'reference.png'
    if not p.is_file():return None
    ref=Image.open(p).convert('RGB');rw,rh=ref.size
    def X(x):return round(x*rw/W)
    def Y(y):return round(y*rh/H)
    # Partition the original artwork into independent chrome / panel plates.
    # Original panels are preserved; the reactor is rendered separately.
    regions=[(0,0,W,74),(0,74,512,352),(0,352,512,570),(0,570,512,756),
             (512,74,1110,140),(512,140,1110,668),(512,668,1110,756),
             (1110,74,W,346),(1110,346,W,548),(1110,548,1373,756),
             (1373,548,W,756),(0,756,W,853),(0,853,W,H)]
    return (rw,rh,[(X(x),Y(y),ref.crop((X(x),Y(y),X(r),Y(b)))) for x,y,r,b in regions])

def skin_frame(t=0.0,command='',tab=0,mic=False,selected='Ana Sayfa',notice='',focus=False):
    plates=reference_plates()
    if plates is None:return frame(t,True,command,tab,mic,selected,notice,focus)
    rw,rh,regions=plates;im=Image.new('RGB',(rw,rh))
    for x,y,plate in regions:im.paste(plate,(x,y))
    def put(patch,box,mask=None):
        x,y,r,b=box;dest=(round(x*rw/W),round(y*rh/H),round(r*rw/W),round(b*rh/H));size=(dest[2]-dest[0],dest[3]-dest[1])
        im.paste(patch.resize(size,Image.Resampling.LANCZOS),dest[:2],mask.resize(size,Image.Resampling.LANCZOS) if mask else None)
    if artwork() is not None:
        # Only the plasma sphere and orbital area are redrawn; outer rings stay fixed.
        core=art_core(t);mask=radial_mask(617,528,326,251,0,170,3);put(core,(498,140,1115,668),mask)
        # The microphone waveform is a separate animated UI region.
        for l,r in [(1168,1231),(1336,1401)]:
            patch=Image.new('RGB',(r-l,44),'#031725');d=ImageDraw.Draw(patch)
            for j in range(5):x=7+j*11;z=4+13*abs(math.sin(t*TAU*3+j*1.3+l));line(d,[(x,22-z),(x,22+z)],CY,4)
            glow=patch.filter(ImageFilter.GaussianBlur(4));put(ImageChops.add(patch,glow),(l,784,r,828))
    # The editable command input, selected task tabs and navigation states are
    # actual Python-rendered overlays. Unchanged reference artwork stays exact.
    if command or focus or tab or mic or selected!='Ana Sayfa' or notice:
        rendered=frame(t,False,command,tab,mic,selected,notice,focus)
        if command or focus:put(rendered.crop((190,790,1081,825)),(190,790,1081,825))
        if tab:put(rendered.crop((1130,138,1617,328)),(1130,138,1617,328))
        if selected!='Ana Sayfa':put(rendered.crop((369,10,1287,58)),(369,10,1287,58))
        if mic:put(rendered.crop((1247,770,1319,844)),(1247,770,1319,844))
        if notice:put(rendered.crop((420,748,1231,775)),(420,748,1231,775))
    return im

class App:
    def __init__(self,mode="skin"):
        import tkinter as tk
        from PIL import ImageTk
        self.tk=tk;self.ImageTk=ImageTk;self.root=tk.Tk();self.root.title('R.A.Y.A • REACTOR HUD — DEMO / örnek veriler');self.root.geometry('1318x742');self.root.minsize(824,464)
        self.canvas=tk.Canvas(self.root,bg='#020a13',highlightthickness=0);self.canvas.pack(fill='both',expand=True);self.item=self.canvas.create_image(0,0,anchor='nw');self.mode=mode;self.compare=False;self.reference=Image.open(HERE/'reference.png').convert('RGB') if (HERE/'reference.png').exists() else None
        self.start=time.monotonic();self.paused=False;self.frozen=0;self.full=False;self.command='';self.focus=False;self.tab=0;self.mic=False;self.selected='Ana Sayfa';self.notice='';self.notice_until=0;self.closed=False;self.rect=(0,0,1,1);self.after=None
        self.root.bind('<F2>',self.toggle);self.root.bind('<F3>',self.comparison);self.root.bind('<F11>',self.fullscreen);self.root.bind('<Escape>',self.escape);self.root.bind('<KeyPress>',self.key);self.canvas.bind('<Button-1>',self.click);self.root.protocol('WM_DELETE_WINDOW',self.close);self.tick()
    def close(self):
        self.closed=True
        if self.after is not None:self.root.after_cancel(self.after)
        self.root.destroy()
    def escape(self,e=None):
        if self.full:self.fullscreen()
        else:self.close()
    def fullscreen(self,e=None):self.full=not self.full;self.root.attributes('-fullscreen',self.full)
    def toggle(self,e=None):
        self.mode={'skin':'procedural','procedural':'hybrid','hybrid':'skin'}[self.mode];self.compare=False;self.toast({'skin':'Referans katmanları — en yakın görünüm','procedural':'Tamamen Python çizimi','hybrid':'Python panelleri + referans çekirdeği'}[self.mode])
    def comparison(self,e=None):
        if self.reference:self.compare=not self.compare
    def toast(self,s):self.notice=s;self.notice_until=time.monotonic()+4
    def submit(self):
        if self.command.strip():self.toast('Demo komutu alındı; gerçek AI çağrısı yapılmadı.');self.command='';self.focus=False
        else:self.toast('Önce bir komut yazın.')
    def key(self,e):
        if e.keysym in ('F2','F3','F11','Escape'):return
        if e.keysym=='Return':self.submit();return 'break'
        if not self.focus and e.keysym=='space':
            if self.paused:self.start=time.monotonic()-self.frozen
            else:self.frozen=time.monotonic()-self.start
            self.paused=not self.paused;return 'break'
        if self.focus:
            if e.keysym=='BackSpace':self.command=self.command[:-1]
            elif e.state & 4 and e.keysym.lower()=='v':
                try:self.command=(self.command+self.root.clipboard_get().replace('\n',' '))[:512]
                except self.tk.TclError:pass
            elif e.char and e.char.isprintable():self.command=(self.command+e.char)[:512]
            return 'break'
    def click(self,e):
        if self.compare:return
        ox,oy,nw,nh=self.rect;x=(e.x-ox)*W/nw;y=(e.y-oy)*H/nh;self.focus=False
        if 124<x<1080 and 779<y<834:self.focus=True;self.canvas.focus_set();return
        if 1438<x<1518 and 779<y<834:self.submit();return
        if 1245<x<1321 and 769<y<844:self.mic=not self.mic;self.toast('Mikrofon animasyonu: demo, ses kaydı yapılmıyor.');return
        if 1080<x<1133 and 779<y<834:
            from tkinter import filedialog
            f=filedialog.askopenfilename(parent=self.root)
            if f:self.toast('Seçilen dosya: '+Path(f).name+' (yükleme yok)')
            return
        if 139<y<172 and 1132<x<1530:self.tab=0 if x<1240 else 1 if x<1370 else 2;return
        if 1580<x<1615 and 180<y<330 and self.tab==0:self.toast('Görev önizlemesi: '+TASKS[min(2,max(0,int((y-180)//52)))]) ;return
        if y<60:
            for n,ic,l,r in NAV:
                if l<x<r:self.selected=n;self.toast(n+' — arayüz önizlemesi');return
            if 1516<x<1567:self.toast('Bildirimler paneli: örnek veriler');return
            if 1618<x<1645 and y<25:self.close();return
            if 1598<x<1618 and y<25:self.fullscreen();return
            if 1578<x<1598 and y<25:self.root.iconify();return
        for l,top,r,b,title,ic in PANELS:
            if l<x<r and top<y<b:self.toast(title+' — demo paneli');return
    def tick(self):
        if self.closed:return
        t0=time.monotonic();t=(self.frozen if self.paused else t0-self.start)/CYCLE_SECONDS
        if self.compare and self.reference:im=self.reference
        else:
            args=(self.command,self.tab,self.mic,self.selected,self.notice if t0<self.notice_until else '',self.focus)
            im=skin_frame(t,*args) if self.mode=='skin' else frame(t,self.mode=='procedural',*args)
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height();s=min(w/W,h/H);nw,nh=max(1,round(W*s)),max(1,round(H*s));ox,oy=(w-nw)//2,(h-nh)//2;self.rect=(ox,oy,nw,nh)
        self.photo=self.ImageTk.PhotoImage(im.resize((nw,nh),Image.Resampling.BILINEAR));self.canvas.itemconfigure(self.item,image=self.photo);self.canvas.coords(self.item,ox,oy)
        self.after=self.root.after(max(12,42-int((time.monotonic()-t0)*1000)),self.tick)
    def run(self):self.root.mainloop()

# The integrated entry point is python -m raya hud.
