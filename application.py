from flask import Flask, render_template, request

import http.client, urllib.request, urllib.parse, urllib.error, base64, json

from bokeh.io import output_file, show
from bokeh.models import (BoxZoomTool, Circle, HoverTool,
                          MultiLine, Plot, Range1d,
                          ResetTool, WheelZoomTool,
                          TapTool, OpenURL)
from bokeh.palettes import Spectral4
from bokeh.plotting import from_networkx
from bokeh.models.graphs import NodesAndLinkedEdges
from bokeh.embed import components
from bokeh.models.callbacks import CustomJS
from bokeh.palettes import OrRd9, Blues9

import networkx as nx

# %%
cm1 = Blues9
cm2 = OrRd9

headers = {
    # Request headers
    'Content-Type': 'application/x-www-form-urlencoded',
    'Ocp-Apim-Subscription-Key': '',
}


# microsoft academic graph requests
class ResponseError(Exception):
    def __init__(self, errno, strerror):
        self.errno = errno
        self.strerror = strerror


def interpret(query):
    params = urllib.parse.urlencode({
        'model': 'latest',
        'count': '100',
        'offset': '0',
        'query': query,
    })

    try:
        conn = http.client.HTTPSConnection('api.labs.cognitive.microsoft.com')
        conn.request("POST", "/academic/v1.0/interpret", params, headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        data_decoded = json.loads(data)
        if 'Error' in data_decoded.keys():
            raise ResponseError(-100, 'got answer but error: ' + str(data_decoded))
        return(data_decoded)
    except Exception as e:
        print("[Errno {0}] {1}".format(e.errno, e.strerror))
        return(None)


def evaluate(query, n=100):
    params = urllib.parse.urlencode({
        # Request parameters
        'model': 'latest',
        'count': n,
        'offset': '0',
        'orderby': '',
        'attributes': 'Id,DN,Y,CC,J.JN,AA.AuN,DOI,RId',
        'expr': query,
    })

    try:
        conn = http.client.HTTPSConnection('api.labs.cognitive.microsoft.com')
        conn.request("POST", "/academic/v1.0/evaluate", params, headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        data_decoded = json.loads(data)
        if 'Error' in data_decoded.keys():
            raise ResponseError(-100, 'got answer but error: ' + str(data_decoded))
        return(data_decoded)
    except Exception as e:
        print("[Errno {0}] {1}".format(e.errno, e.strerror))
        return(None)


def prepare_data(query, n):
    # %% convert the natural language request to a query
    interpret_data = interpret(query)
    # %% get the most likely query result
    if 'interpretations' not in interpret_data.keys():
        return 0, 0
    exprs = [
        e['rules'][0]['output']['value']
        for e in interpret_data['interpretations']
        if e['rules'][0]['output']['type'] == 'query']
    eval_data = evaluate(exprs[0], n=n)
    if 'entities' not in eval_data.keys():
        return 0, 0
    # %% process primary found papers
    papers = [e for e in eval_data['entities']]
    # strip incomplete
    papers = [p for p in papers if 'DN' in p.keys()]
    papers = [p for p in papers if 'AA' in p.keys()]
    #papers = [p for p in papers if 'AuN' in p['AA'].keys()]
    papers = [p for p in papers if 'J' in p.keys()]
    #papers = [p for p in papers if 'JN' in p['JN'].keys()]
    papers = [p for p in papers if 'Y' in p.keys()]
    papers = [p for p in papers if 'CC' in p.keys()]
    for p in papers:
        if 'DOI' not in p.keys():
            p['DOI'] = 'unknown'
    max_cit = max([p['CC'] for p in papers])
    # create parallel array as this is the main identifier,
    # should make sure to retain ordering stays aligned along the script
    ids = [p['Id'] for p in papers]
    # extract all references
    rids = []
    _ = [rids.extend(ridsl) for ridsl in [p['RId'] for p in papers if 'RId' in p.keys()]]
    # get rid of reference ids that are already in the primary request
    rids = [rid for rid in rids if rid not in ids]

    # %% get the secondary found papers information
    expr_ref = "Or(Id=" + ",Id=".join([str(rdi) for rdi in rids]) + ")"
    eval_data_ref = evaluate(expr_ref, n=1000)
    if eval_data_ref is not None:
        # %% process secondary found papers
        papers_ref = [e for e in eval_data_ref['entities']]

        # strip incomplete
        papers_ref = [p for p in papers_ref if 'DN' in p.keys()]
        papers_ref = [p for p in papers_ref if 'AA' in p.keys()]
        #papers = [p for p in papers if 'AuN' in p['AA'].keys()]
        papers_ref = [p for p in papers_ref if 'J' in p.keys()]
        #papers = [p for p in papers if 'JN' in p['JN'].keys()]
        papers_ref = [p for p in papers_ref if 'Y' in p.keys()]
        papers_ref = [p for p in papers_ref if 'CC' in p.keys()]
        for p in papers_ref:
            if 'DOI' not in p.keys():
                p['DOI'] = 'unknown'
        max_cit_ref = max([p['CC'] for p in papers_ref])
        ids_ref = [p['Id'] for p in papers_ref]


        #rids_ref = []
        #_ = [rids_ref.extend(ridsl) for ridsl in [e['RId'] for e in papers_ref if 'RId' in e.keys()]]
        #rids_ref = [rid for rid in rids_ref if rid not in ids]
    else:
        # print('no refdata')
        papers_ref = []
        ids_ref = []
        max_cit_ref = 0
    
    # max_cit = max([max_cit, max_cit_ref])
    # %%
    G = nx.Graph()
    # add primary papers
    for id, paper in zip(ids, papers):
        color = cm2[int(8*(1-paper['CC']/max_cit))]
        G.add_node(
            id,
            type='primary',
            color=color,
            title=paper['DN'],
            authors=', '.join([a['AuN'] for a in paper['AA']]),
            journal=paper['J']['JN'],
            year=paper['Y'],
            DOI=paper['DOI'],
            size=20)
    # add their references
    for id, paper in zip(ids_ref, papers_ref):
        color = cm1[int(8*(1-paper['CC']/max_cit_ref))]
        G.add_node(
            id,
            type='reference',
            color=color,
            title=paper['DN'],
            authors=', '.join([a['AuN'] for a in paper['AA']]),
            journal=paper['J']['JN'],
            year=paper['Y'],
            DOI=paper['DOI'],
            size=10)

    # add connections from primaries to references
    for p in papers:
        if 'RId' in p.keys():
            # between primaries
            G.add_edges_from([(p['Id'], rid) for rid in p['RId'] if rid in ids])
            # between primaries and references
            G.add_edges_from([(p['Id'], rid) for rid in p['RId'] if rid in ids_ref])
    # add connections between references
    for p in papers_ref:
        if 'RId' in p.keys():
            G.add_edges_from([(p['Id'], rid) for rid in p['RId'] if rid in ids_ref])
    return G, exprs[0]


def draw_plot(G, query, expr):
    plot = Plot(
        x_range=Range1d(-1.1, 1.1), y_range=Range1d(-1.1, 1.1),
        sizing_mode="stretch_both")
    plot.title.text = "Relationship Graph for: " + expr

    tooltips = """
        <div style="max-width : 300px">
                <div><span style="">@type</span></div>
                <div><span style="font-weight: bold;">@title</span></div>
                <div><span style="">@authors</span></div>
                <div><span style="font-weight: bold;">@journal</span></div>
                <div><span style="font-weight: bold;">@year</span></div>
                <div><span style=""><a href="https://doi.org/@DOI">@DOI</a></span></div>
        </div>
    """

    node_hover_tool = HoverTool(tooltips=tooltips)
    zoom_tool = WheelZoomTool()
    tap_tool_open = TapTool()
    #tap_tool_open.callback = OpenURL(url='https://doi.org/@DOI')
    #tap_tool_open.callback = OpenURL(
    #    same_tab=True,
    #    url="javascript:document.getElementById('showpaper').innerHTML='" + tooltips + "';")
    #tap_tool_open.callback = CustomJS(
    #    args=dict(source=0, selected=0),
    #    code = """document.getElementById('showpaper').innerHTML='""" + ''.join(tooltips.splitlines()) + "';")
    
    showpaper = """
        <div style="max-width : 80%">
                <div><span style="">@type</span></div>
                <div><span style="font-weight: bold;">@title</span></div>
                <div><span style="">@authors</span></div>
                <div><span style="font-weight: bold;">@journal</span></div>
                <div><span style="font-weight: bold;">@year</span></div>
                <div><span style=""><a target="_blank" href="https://doi.org/@DOI">@DOI</a></span></div>
        </div>
    """

    code = '''  if (cb_data.source.selected.indices.length > 0){
                    var selected_index = cb_data.source.selected.indices[0];
                    var tooltip = document.getElementById("showpaper");
                    cb_data.source.data.color[selected_index] = 'grey'
                    tp = tp.replace('@type', cb_data.source.data.type[selected_index]);
                    tp = tp.replace('@title', cb_data.source.data.title[selected_index]);
                    tp = tp.replace('@authors', cb_data.source.data.authors[selected_index]);
                    tp = tp.replace('@journal', cb_data.source.data.journal[selected_index]);
                    tp = tp.replace('@year', cb_data.source.data.year[selected_index]);
                    tp = tp.replace('@DOI', cb_data.source.data.DOI[selected_index]);
                    tp = tp.replace('@DOI', cb_data.source.data.DOI[selected_index]);
                    tooltip.innerHTML = tp;
            } '''
    
    tap_tool_open.callback = CustomJS(
        args = {'tp': showpaper}, code = code)

    tap_tool = TapTool()

    plot.add_tools(
        node_hover_tool,
        zoom_tool,
        # BoxZoomTool(),
        ResetTool(),
        tap_tool_open,
        tap_tool
    )
    plot.toolbar.active_scroll = zoom_tool

    graph_renderer = from_networkx(
        G, nx.spring_layout, scale=1, center=(0, 0), seed=12345)

    # normal
    graph_renderer.node_renderer.glyph = Circle(
        size="size", fill_color="color")
    graph_renderer.edge_renderer.glyph = MultiLine(
        line_alpha=0.2)

    # selection
    graph_renderer.node_renderer.selection_glyph = Circle(
        fill_color="color", fill_alpha=1, line_alpha=1)
    graph_renderer.edge_renderer.selection_glyph = MultiLine(
        line_width=3, line_alpha=1)
    graph_renderer.node_renderer.nonselection_glyph = Circle(
        fill_color="color", fill_alpha=0.5, line_alpha=0.5)
    graph_renderer.edge_renderer.nonselection_glyph = MultiLine(
        line_alpha=0.2)

    # hover
    graph_renderer.node_renderer.hover_glyph = Circle(
        fill_color='#abdda4')
    graph_renderer.edge_renderer.hover_glyph = MultiLine(
        line_color='#abdda4', line_width=3)

    graph_renderer.inspection_policy = NodesAndLinkedEdges()
    graph_renderer.selection_policy = NodesAndLinkedEdges()

    plot.renderers.append(graph_renderer)

    script, div = components(plot)
    return script, div


# get common data
query = 'metasurface'
metasurface_graph, metasurface_expr = prepare_data(query, n=20)
metasurface_plot_script, metasurface_plot_div = draw_plot(
    metasurface_graph, query, metasurface_expr)

# website app
app = Flask(__name__)
# serve landing page
@app.route("/")
def hello():
    query = request.args.get("query")
    n = request.args.get("n")

    if type(n) == type(None):
        n = 20
    else:
        n = int(n)
    if n > 100:
        n = 100

    if type(query) == type(None):
        return render_template(
            "index_template.html",
            script=metasurface_plot_script,
            div=metasurface_plot_div,
            query='metasurface',
            ns=["10", "20", "50"],
            cn=str(n))
    else:
        graph, expr = prepare_data(query, n=n)
        plot_script, plot_div = draw_plot(graph, query, expr)
        return render_template(
            "index_template.html",
            script=plot_script,
            div=plot_div,
            query=query,
            ns=["10", "20", "50"],
            cn=str(n))


    # %%
