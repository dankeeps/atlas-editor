// Juntas do corpo (Vision) para cada imagem de uma pasta. Saída: JSON {arquivo: {junta: [x, y, confiança]}}.
// x/y vêm normalizados com origem embaixo-esquerda, como o Vision devolve.
ObjC.import('Foundation'); ObjC.import('Vision'); ObjC.import('CoreImage');
var JUNTAS = ["nose", "neck_1_joint", "left_shoulder_1_joint", "right_shoulder_1_joint",
              "left_upLeg_joint", "right_upLeg_joint", "root"];
function run(argv){
  var dir = argv[0], fm = $.NSFileManager.defaultManager;
  var files = ObjC.deepUnwrap(fm.contentsOfDirectoryAtPathError(dir, null))
                  .filter(function(f){ return /\.(jpg|png)$/.test(f) }).sort();
  var out = {};
  files.forEach(function(f){
    var ci = $.CIImage.imageWithContentsOfURL($.NSURL.fileURLWithPath(dir + '/' + f));
    var req = $.VNDetectHumanBodyPoseRequest.alloc.init;
    var h = $.VNImageRequestHandler.alloc.initWithCIImageOptions(ci, $());
    h.performRequestsError($([req]), null);
    var pessoas = [];
    var n = req.results ? req.results.count : 0;
    for (var i = 0; i < n; i++) {                       // pode haver gente ao fundo; quem escolhe é o motor
      var obs = req.results.objectAtIndex(i), j = {};
      JUNTAS.forEach(function(nome){
        var p = obs.recognizedPointForJointNameError($(nome), null);
        if (p && !p.isNil() && p.confidence > 0) j[nome] = [p.location.x, p.location.y, p.confidence];
      });
      pessoas.push(j);
    }
    out[f] = pessoas;
  });
  return JSON.stringify(out);
}
