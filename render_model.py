#!/usr/bin/env python3
"""Background renderer: official generate_icons + source Placement/Line.
The exporter graphics code is unchanged; OMC queries use the current session API.
This is not OMEdit GUI export.
"""
import argparse
import copy
import importlib.util
import json
import logging
from pathlib import Path
import re
import sys


def install_exporter_queries(exporter):
    """Bridge old exporters to OMPython 4 without legacy parser state methods.

    Older generate_icons.ask_omc calls clearOMParserResult.
    OMCSessionZMQ.sendExpression already returns the parsed result. Keep raw
    annotation requests raw and retain the exporter's per-session query cache.
    Legacy count/nth inheritance queries are mapped to getInheritedClasses;
    recent compilers can return false for the deprecated getNthInheritedClass.
    """
    cache = {}

    def ask_omc(question, opt=None, parsed=True):
        key = (question, opt, parsed)
        if key not in cache:
            expression = question + '(' + opt + ')' if opt else question
            try:
                if parsed and question == 'getInheritanceCount':
                    cache[key] = len(ask_omc('getInheritedClasses', opt))
                elif parsed and question == 'getNthInheritedClass':
                    class_name, index = opt.rsplit(',', 1)
                    cache[key] = ask_omc('getInheritedClasses', class_name.strip())[int(index.strip()) - 1]
                else:
                    cache[key] = exporter.omc.sendExpression(expression, parsed=parsed)
            except Exception as exc:
                raise RuntimeError('OpenModelica query failed: ' + expression) from exc
        return cache[key]

    exporter.ask_omc = ask_omc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--class-name', default='')
    parser.add_argument('--exporter', type=Path, default=Path('/usr/share/doc/omc/testmodels/generate_icons.py'))
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    out = args.output.resolve();out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('official_generate_icons', args.exporter)
    exporter = importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s', handlers=[logging.FileHandler(out/'generation.log',mode='w'),logging.StreamHandler()])
    exporter.logger = logging.getLogger('generate_icons')
    install_exporter_queries(exporter)
    exporter.use_subdirs = False
    exporter.output_dir = str(out/'icons');Path(exporter.output_dir).mkdir(exist_ok=True)
    exporter.baseDir = str(args.library.resolve().parent)
    import svgwrite
    import cairosvg
    try:
        from importlib.metadata import version
        print('OpenModelica:', exporter.omc.sendExpression('getVersion()'), flush=True)
        print('OMPython:', version('OMPython'), flush=True)
        print('Icon exporter:', args.exporter.resolve(), flush=True)
        for command in exporter.OMC_SETUP_COMMANDS:
            exporter.omc.sendExpression(command)
        for file in [args.library.resolve(), args.model.resolve()]:
            if not exporter.omc.sendExpression('loadFile('+json.dumps(str(file), ensure_ascii=False)+')'):
                raise RuntimeError('Could not load ' + str(file) + ': ' + exporter.omc.sendExpression('getErrorString()', parsed=False))
        exporter.omc.sendExpression('loadModel(Modelica)')
        if not args.class_name:
            classes=exporter.omc.sendExpression('parseFile('+json.dumps(str(args.model.resolve()), ensure_ascii=False)+')')
            if not classes:raise RuntimeError('No Modelica class found')
            args.class_name=classes[0]
        warnings=[]
        print('Loaded model:',args.class_name,flush=True)
        infos = exporter.unparseArrays(exporter.ask_omc('getComponents', args.class_name+', useQuotes=true', parsed=False))
        annotations = exporter.getStrings(exporter.removeFirstLastCurlBrackets(exporter.ask_omc('getComponentAnnotations',args.class_name,parsed=False)))
        print('Model declarations:', len(infos), '; annotation records:', len(annotations), flush=True)
        components=[];native={}
        for info, annotation in zip(infos,annotations):
            info = exporter.unparseStrings(info)
            if len(info)<2:continue
            kind,name=info[:2]
            placement=exporter.componentPlacement(annotation)
            if not placement:
                if '.' in kind:warnings.append('Missing Placement annotation for '+name)
                continue
            if placement[0]!='true':continue
            values=[float(x) if x not in ('-','') else 0 for x in placement[1:8]]
            ox,oy,x1,y1,x2,y2,rotation=values
            bases=[];exporter.getBaseClasses(kind,bases)
            if kind not in native:
                native[kind]=[copy.deepcopy(exporter.getGraphicsWithPortsForClass(c)) for c in [*reversed(bases),kind]]
            components.append({'name':name,'type':kind,'origin':[ox,oy],'extent':[[x1,y1],[x2,y2]],'rotation':rotation})
            print('Exported component',name,kind,flush=True)
        from visualization import clean_source
        source=clean_source(args.model.read_text())
        connections=[]
        for match in re.finditer(r'connect\s*\(([^,]+),([^)]*)\)\s*annotation\s*\(\s*Line\((.*?)\)\s*\)\s*;',source,re.S):
            attrs=match[3]
            points_match=re.search(r'points\s*=\s*\{(.*?)\}\s*(?:,|$)',attrs,re.S)
            if not points_match:continue
            # Match the entire nested point list before the next named attribute.
            points_match=re.search(r'points\s*=\s*(\{\{.*?\}\})',attrs,re.S)
            if not points_match:
                warnings.append('A connection has no supported Line points')
                continue
            pairs=re.findall(r'\{\s*([-+\deE.]+)\s*,\s*([-+\deE.]+)\s*\}',points_match[1])
            points=[[float(x),float(y)] for x,y in pairs]
            color=re.search(r'color\s*=\s*\{(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\}',attrs)
            connections.append({'from':match[1].strip(),'to':match[2].strip(),'points':points,'color':list(map(int,color.groups())) if color else [0,0,255]})
        if not components:raise RuntimeError('No placed components found. Generate Placement annotations before rendering.')
        expected_connections=int(exporter.omc.sendExpression('getConnectionCount('+args.class_name+')'))
        if expected_connections>len(connections):warnings.append(str(expected_connections-len(connections))+' connections have no supported Line annotation and are not drawn.')
        xs=[c['origin'][0] for c in components]+[p[0] for c in connections for p in c['points']]
        ys=[c['origin'][1] for c in components]+[p[1] for c in connections for p in c['points']]
        left,right,bottom,top=min(xs)-40,max(xs)+40,min(ys)-35,max(ys)+40
        width,height=right-left,top-bottom
        path=out/'diagram.svg'
        dwg=svgwrite.Drawing(str(path),size=(round(width*6),round(height*6)),viewBox=f'{left} {-top} {width} {height}')
        dwg.add(dwg.rect((left,-top),(width,height),fill='white'))
        for connection in connections:
            line=dwg.polyline([(x,-y) for x,y in connection['points']],fill='none',stroke='rgb(%d,%d,%d)'%tuple(connection['color']),stroke_width=.45)
            line.set_desc(title=connection['from']+' ↔ '+connection['to']);dwg.add(line)
        exporter.element_id=0
        for component in components:
            icons=copy.deepcopy(native[component['type']])
            ox,oy=component['origin'];(x1,y1),(x2,y2)=component['extent']
            (u1,v1),(u2,v2)=icons[-1]['coordinateSystem']['extent']
            sx,sy=(x2-x1)/(u2-u1),(y2-y1)/(v2-v1)
            group=dwg.g(transform=f'translate({ox},{-oy}) rotate({-component["rotation"]}) translate({(x1+x2)/2},{-(y1+y2)/2}) scale({sx},{sy}) translate({-(u1+u2)/2},{(v1+v2)/2})')
            group.set_desc(title=component['name']+' : '+component['type'])
            def draw(graphics, transformation=None, coords=None):
                if graphics.get('visible') in (False,'false'):return
                if 'textString' in graphics:
                    graphics['textString']=graphics['textString'].replace('%name',component['name']).replace('%class',component['type'].split('.')[-1])
                shapes=exporter.getSvgFromGraphics(dwg,graphics,0,0,False,transformation,coords)
                if shapes:
                    group.add(shapes[0]);group.add(shapes[1])
            for icon in icons:
                for graphic in icon['graphics']:draw(graphic)
            for icon in icons:
                for port in icon['ports']:
                    for graphic in port['graphics']:draw(graphic,port['transformation'],port['coordinateSystem'])
            dwg.add(group)
        dwg.save()
        png=path.with_suffix('.png')
        cairosvg.svg2png(url=str(path),write_to=str(png),output_width=1600)
        (out/'metadata.json').write_text(json.dumps({'class':args.class_name,'exporter':str(args.exporter),'components':len(components),'connections':len(connections),'warnings':warnings,'width':round(width*6),'height':round(height*6)},indent=2))
        print('DONE',len(components),'components,',len(connections),'annotated connections',flush=True)
        print(path);print(png)
    finally:
        try:exporter.omc.sendExpression('quit()')
        except Exception:pass


if __name__=='__main__':main()
