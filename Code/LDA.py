import numpy as np
from parseMovies import parseMovies
from parseData import get_split_review_mats
from parseData import getMeta
from random import randint, shuffle, sample
from itertools import izip, product
import math
from collections import defaultdict
from matplotlib.mlab import PCA
from matplotlib import pyplot as plt
import logging
import logging.handlers
import datetime
import time
import sys


class GibbsSampler(object):

    def __init__(self, numTopics, alpha, beta, gamma):
        # Setup logger
        self.log = logging.getLogger("Gibbs")
        self.log.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s %(message)s",
                                      datefmt="%m/%d/%Y %I:%M:%S %p")
        fh = logging.handlers.TimedRotatingFileHandler("logs/gibbs.log",
                                                       when="D",
                                                       interval=1,
                                                       backupCount=10)
        ch = logging.StreamHandler()
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        self.log.addHandler(fh)
        self.log.addHandler(ch)

        self.numTopics = numTopics
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma

        self.info = getMeta()

        self.user_movies, _ = get_split_review_mats()
        user_indices, movie_indices = self.user_movies.nonzero()
        self.user_movie_indices = zip(user_indices, movie_indices)

        self.CountMT = np.zeros((self.info["movies"], numTopics), dtype=np.int)
        self.CountRUT = np.zeros((6, self.info["users"], numTopics), dtype=np.int)  # ratings 1-5 and 0
        self.CountUT = np.zeros((self.info["users"], numTopics), dtype=np.int)
        self.topic_assignments = np.zeros((self.info["users"], self.info["movies"]), dtype=np.int)

        # Normalization factors
        self.CountT = np.zeros(numTopics, dtype=np.int)
        self.CountU = np.zeros(self.info["users"], dtype=np.int)
        self.CountRU = np.zeros((6, self.info["users"]), dtype=np.int)

        for userid, movieid in self.user_movie_indices:
            topic = randint(0, numTopics - 1)
            self.CountMT[movieid, topic] += 1
            rating = self.user_movies[userid, movieid]
            self.CountRUT[rating, userid, topic] += 1
            self.CountUT[userid, topic] += 1
            self.topic_assignments[userid, movieid] = topic

            self.CountT[topic] += 1
            self.CountU[userid] += 1
            self.CountRU[rating, userid] += 1

    def run(self, total_iters, burn_in, thinning):
        self.log.info("Starting Gibbs Sampling for %d iterations with %d users, %d movies, %d ratings, %d topics",
                      total_iters, self.info["users"], self.info["movies"],
                      self.info["ratings"], self.numTopics)
        log_likelihoods = []
        collection = total_iters*(1-burn_in) / thinning
        self.theta_collection = np.empty( (collection, self.info["users"], self.numTopics) )
        self.phi_collection = np.empty( (collection, self.info["movies"], self.numTopics) )
        self.kappa_collection = np.empty( (collection, 6, self.info["users"], self.numTopics) )
        for currIter in xrange(total_iters):
            shuffle(self.user_movie_indices)
            for userid, movieid in self.user_movie_indices:

                # Unassign previous topic
                prev_topic = self.topic_assignments[userid, movieid]
                rating = self.user_movies[userid, movieid]
                self.CountMT[movieid, prev_topic] -= 1
                self.CountUT[userid, prev_topic] -= 1
                self.CountRUT[rating, userid, prev_topic] -= 1

                # Unassign normalization factors
                self.CountT[prev_topic] -= 1
                self.CountU[userid] -= 1
                self.CountRU[rating, userid] -= 1

                # Get probability distribution for (user, movie) over topics
                topic_probs = self.getTopicProb(userid, movieid, rating)

                # Normalize
                topic_probs = topic_probs / sum(topic_probs)

                # Sample new topic
                new_topic = np.random.choice(self.numTopics, 1, p=topic_probs)

                self.topic_assignments[userid, movieid] = new_topic
                # Update new topic assignments
                self.CountMT[movieid, new_topic] += 1
                self.CountUT[userid, new_topic] += 1
                self.CountRUT[rating, userid, new_topic] += 1

                # Assign normalization factors
                self.CountT[new_topic] += 1
                self.CountU[userid] += 1
                self.CountRU[rating, userid] += 1

            if currIter >= (total_iters * burn_in):
                if ((currIter - total_iters*burn_in) % thinning) == 0:
                    idx = (currIter - total_iters*burn_in) / thinning
                    ll = self.logLike(idx)
                    log_likelihoods.append(ll)
                    self.log.info("Iteration %d: %.4f", currIter, ll)
            print 'Done with iter %d' % currIter
            '''
            if (currIter + 1) % 5 == 0:
                fig = self.visualizePCA()
                fig.savefig("figs/tmp/%.4f.jpeg" % ll, format="jpeg", dpi=300)
                topics = self.genMostLikelyTopic()
                for topicid in topics:
                    # Write out the movies whose top topic is topicid
                    # we sort these moves to show the ones that weigh topicid
                    # largest
                    self.log.info("Topic: %d\n\t%s", topicid,
                                  "\n\t".join("%s (%.4f)" % (title, p)
                                              for title, p in
                                              sorted(topics[topicid], key=lambda x: -x[1])[:10]))
                    print ""

                fig = self.graph_loglike(log_likelihoods)
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                fig.savefig("figs/ll-%s.jpeg" % ts, format="jpeg")
            '''
        np.savez('result_lda/result.npz', phi_collection=self.phi_collection,
                 theta_collection=self.theta_collection, kappa_collection=self.kappa_collection)
        np.save('result_lda/ll.npy', np.asarray(log_likelihoods))

    def graph_loglike(self, log_likelihoods):
        fig = plt.figure()
        plt.plot(range(1, len(log_likelihoods) + 1), log_likelihoods)
        return fig

    def logLike(self, idx):
        phi = self.calcPhi()
        kappa = self.calcKappa()
        theta = self.calcTheta()
        ll = 0

        for userid, movieid in self.user_movie_indices:
            topic = self.topic_assignments[userid, movieid]
            rating = self.user_movies[userid, movieid]
            ll += math.log(phi[movieid, topic])
            ll += math.log(kappa[rating, userid, topic])
            ll += math.log(theta[userid, topic])

        gamma_alpha = self.numTopics*math.lgamma(self.alpha) - math.lgamma(self.numTopics*self.alpha)
        for userid in xrange(self.info["users"]):
            ll += -gamma_alpha + (self.alpha-1)*np.sum(np.log(theta[userid,:]))

        gamma_phi = self.numTopics*math.lgamma(self.beta) - math.lgamma(self.numTopics*self.beta)
        for movieid in xrange(self.info["movies"]):
            ll += -gamma_phi + (self.beta-1)*np.sum(np.log(phi[movieid,:]))

        gamma_kappa = 5*math.lgamma(self.gamma) - math.lgamma(5*self.gamma)
        for userid, topic in product(xrange(self.info["users"]), xrange(self.numTopics)):
            ll += -gamma_kappa + (self.gamma-1)*np.sum(np.log(kappa[1:,userid, topic]))

        # Dirichlet PDF can be > 1, so ignore this
        # try:
        #     assert ll < 0
        # except AssertionError:
        #     self.log.error("Log likelihood %.4f greater than 0", ll)
        #     self.log.error("Gamma_alpha: %.4f", gamma_alpha)
        #     self.log.error("Gamma_phi: %.4f", gamma_phi)
        #     self.log.error("Gamma_kappa: %.4f", gamma_kappa)

        #     raise

        self.theta_collection[idx, :, :] = theta
        self.kappa_collection[idx, :, :, :] = kappa
        self.phi_collection[idx, :, :] = phi

        return ll

    def calcTheta(self):
        theta = (self.CountUT + self.alpha)
        theta = theta.astype(np.float)
        norm = self.CountT + self.info["users"]*self.alpha
        norm = norm.astype(np.float)

        for topic in xrange(self.numTopics):
            theta[:,topic] /= norm[topic]

        return theta

    def calcPhi(self):
        phi = (self.CountMT + self.beta)
        phi = phi.astype(np.float)
        norm = self.CountT + self.info["movies"]*self.beta
        norm = norm.astype(np.float)

        for topic in xrange(self.numTopics):
            phi[:, topic] /= norm[topic]

        return phi

    def calcKappa(self):
        kappa = (self.CountRUT + self.gamma)
        norm = (self.CountRU + self.numTopics * self.gamma)

        for rating, userid in product(xrange(1, 6), xrange(self.info["users"])):
            kappa[rating, userid, :] /= norm[rating, userid]

        return kappa

    def getTopicProb(self, userid, movieid, rating):
        p_mt = (self.CountMT[movieid, :] + self.beta) / (self.CountT + self.info["movies"] * self.beta)
        p_ut = (self.CountUT[userid, :] + self.alpha) / (self.CountU[userid] + self.numTopics * self.alpha)
        p_rut = (self.CountRUT[rating, userid, :] + self.gamma) / (self.CountRU[rating, userid] + self.numTopics * self.gamma)
        return p_ut * p_rut * p_mt

    def genMostLikelyTopic(self):
        phi = self.calcPhi()
        movies = parseMovies()
        topics = defaultdict(list)
        for movieid in xrange(self.info["movies"]):
            top_topic = np.argsort(phi[movieid, :])[-1]

            topics[top_topic].append((movies[movieid][0],
                                     phi[movieid, top_topic]))
        return topics

    def genMostLikelyMovies(self):
        movies = parseMovies()
        phi = self.calcPhi()
        for topic in xrange(15):
            top_movies = np.argsort(phi[:, topic])
            print "Topic: %d" % topic
            print "\n".join("%s: %.4f" % (movies[movieid][0], phi[movieid, topic]) for movieid in top_movies[-10:])
            print ""

    def visualizePCA(self, samples=20):
        phi = self.calcPhi()
        movies = parseMovies()
        pca = PCA(phi)

        indices = sample(xrange(len(movies)), samples)

        x_axis = pca.Y[indices, 0]
        y_axis = pca.Y[indices, 1]

        fig = plt.figure()
        fig.set_size_inches(10, 8)
        ax = fig.add_subplot(111)

        ax.scatter(x_axis, y_axis)
        for idx, x, y in izip(indices, x_axis, y_axis):
            ax.annotate(movies[idx][0].decode('ascii', 'ignore').encode('ascii', 'ignore'), (x, y))
        return fig

if __name__ == "__main__":
    numTopics = int(sys.argv[1])
    numIters = int(sys.argv[2])
    burn_in = float(sys.argv[3])
    thinning = int(sys.argv[4])

    try:
        alpha = float(sys.argv[5])
    except:
        print "Using default value for alpha = 0.1"
        alpha = 0.1

    try:
        beta = float(sys.argv[6])
    except:
        print "Using default value for beta = 0.01"
        beta = 0.01

    try:
        gamma = float(sys.argv[7])
    except:
        print "Using default value for gamma = 0.9"
        gamma = 0.9

    with open("result_lda/desc.txt", "w") as f:
        f.write("topics: %d\niters: %d\nburn in: %.4f\nthinning: %d\nalpha: %.4f\nbeta: %.4f\ngamma: %.4f"
                % (numTopics, numIters, burn_in, thinning, alpha, beta, gamma))

    sampler = GibbsSampler(numTopics, alpha, beta, gamma)
    sampler.run(numIters, burn_in, thinning)
    # sampler.genMostLikelyMovies()
    # sampler.visualizePCA()
    # topics = sampler.genMostLikelyTopic()
    # for topicid in topics:
    #     print "Topic: %d" % topicid
    #     print "\n".join(title for title, p in topics[topicid][:10])
    #     print ""
